"""3D-серия, авторский план и озвучка под темп: «План:» в бите сильнее режиссёра, проём виден и держится открытым до
«сомкнулись», луч не бьёт в объектив, повторённая реплика звучит одним куском, старые задания эпизода не рендерятся,
слова-ловушки не срабатывают внутри других слов. Без Blender и сети (ffmpeg нужен)."""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest

from app import render_queue as RQ
from app import series3d_blender as B
from app import series3d_voice as VF
from app import stories as S
from app import stories_3d as S3D
from app import stories_gen as X
from test_series3d import LIFT, _words
from test_stories import env  # noqa: F401 - фикстура
from test_stories import _setup

SENTENCES = ["На табло 23 этаж.", "Даня раздвинул двери шире и посветил телефоном.", "Наверху край плиты и закрытая дверь этажа.",
             "А там, откуда шёл звук, голая стена.", "Человеку там негде встать.", "Он отпустил створки, и они сомкнулись.",
             "«Вы где?» — крикнул он.", "Голос ответил сразу: спасатели, держитесь, ещё 5 минут.", "Спасатели, держитесь, ещё 5 минут.",
             "Он выключил фонарик и сел в угол.", "На часах 23:36, никого."]
PLANS = ["смотрит на табло. План: табло из-за плеча", "раздвигает двери руками. План: сверху",
         "светит в проём, тревога. План: за плечом, проём в кадре", "светит, страх. План: сверху, проём в кадре",
         "светит, страх. План: лицо", "вздрагивает. План: за плечом, двери", "кричит, рот двигается. План: лицо",
         "слушает, тревога. План: сверху, потом лицо", "слушает. План: сверху", "садится на пол, усталость. План: лицо",
         "телефон в руке, на экране 23:36. План: деталь"]


def _script() -> dict:
    return {"title": "Лифт", "sentences": [{"text": t, "role": ""} for t in SENTENCES]}


def _beats(plans=PLANS) -> list[dict]:
    return [{"i": n, "sentence": n, "text": SENTENCES[n], "spec": {"action": a}} for n, a in enumerate(plans)]


def _authored_job(beats=None, gemini=False, client=None) -> dict:
    words = _words(_script())
    return B.build_job(_script(), words, float(words[-1]["end"]) + 2.0, gemini=gemini, client=client, beats=beats or _beats())


class _Boom:
    def generate_content(self, *_a, **_k):
        raise AssertionError("режиссёра спрашивать незачем")


class _Director:
    """Режиссёр-подделка: на всё предлагает одно и то же, чтобы отличить его выбор от авторского."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def generate_content(self, *_a, **_k):
        self.calls += 1
        import json

        shots = [{"n": k, "action": "wait", "emotion": "calm", "camera": "face", "speaker": "narrator", "location": "shaft"}
                 for k in range(self.n)]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({"shots": shots})}]}}]}


# --------------------------------------------------------------------------- #
# Разбор плана
# --------------------------------------------------------------------------- #

def test_beat_plan_parses_author_words():
    plan = B.beat_plan({"spec": {"action": "светит, страх. План: сверху, проём в кадре"}})
    assert plan == {"cameras": ["overhead"], "frame": "doorway", "action": "phone_light", "emotion": "scared"}
    assert B.beat_plan({"spec": {"action": "смотрит в камеру. План: лицо, затем сверху"}})["cameras"] == ["face", "overhead"]
    assert B.beat_plan({"spec": {"action": "слушает. План: сверху, потом лицо"}})["cameras"] == ["overhead", "face"]
    shoulder = B.beat_plan({"spec": {"action": "смотрит на табло. План: табло из-за плеча"}})
    assert shoulder["cameras"] == ["detail"] and shoulder["frame"] == "display" and shoulder["action"] == "look_display"
    assert B.beat_plan({"spec": {"action": "вздрагивает. План: за плечом, двери"}})["frame"] == "doors"
    knock = B.beat_plan({"spec": {"action": "встаёт, стучит кулаком. План: сверху"}})
    assert knock["action"] == "knock"
    assert B.beat_plan({"spec": {"action": "герой просто идёт по коридору"}}) is None, "без «План:» решает режиссёр"
    assert B.beat_plan({"spec": {}}) is None and B.beat_plan({}) is None


def test_plan_wins_over_director_only_where_author_speaks():
    beats = _beats()
    beats[2] = {**beats[2], "spec": {"action": "тут автор молчит"}}      # третий бит без плана
    client = _Director(40)
    job = _authored_job(beats, gemini=True, client=client)
    assert client.calls == 1, "есть кадры без плана: режиссёр нужен"
    shots = {s["text"]: s for s in job["shots"]}
    door = next(s for s in job["shots"] if "голая стена" in s["text"])
    assert door["camera"] == "overhead" and door["action"] == "phone_light" and door["frame"] == "doorway"
    assert door["location"] == "cabin", "план автора режиссёр из кабины не уводит"
    assert door["by"] == "author" and door["emotion"] == "scared"
    silent = next(s for t, s in shots.items() if "край плиты" in t)
    assert silent["by"] == "gemini" and silent["action"] == "wait", "там, где автор молчит, выбор режиссёра остаётся"


def test_all_planned_beats_need_no_director():
    job = _authored_job(gemini=True, client=_Boom())
    assert job["director"] == "author" and job["director_error"] == ""
    assert [s["camera"] for s in job["shots"]][:3] == ["detail", "overhead", "ots"]
    assert all(s["by"] == "author" for s in job["shots"])


def test_two_cameras_cut_one_beat_into_two_shots():
    job = _authored_job()
    parts = [s for s in job["shots"] if s["text"].startswith(("Голос ответил", "держитесь", "спасатели"))
             or "ответил" in s["text"] or "минут" in s["text"]][:2]
    assert [p["camera"] for p in parts] == ["overhead", "face"]
    assert parts[0]["t1"] == parts[1]["t0"]
    assert " ".join(p["text"] for p in parts) == SENTENCES[7], "резка по словам, ничего не теряется"


def test_episode_without_plan_unchanged():
    words = _words()
    plain = B.plan_shots(LIFT, [dict(w) for w in words], float(words[-1]["end"]) + 2.0, gemini=False)
    same = B.plan_shots(LIFT, [dict(w) for w in words], float(words[-1]["end"]) + 2.0, gemini=False,
                        beats=[{"i": 0, "sentence": 0, "text": "x", "spec": {"action": "смотрит куда-то"}}])
    assert plain["shots"] == same["shots"] and plain["director"] == same["director"]
    assert all(not s.get("frame") and s["by"] == "rules" for s in plain["shots"])


def test_fix_sequence_keeps_author_camera_and_is_idempotent():
    job = _authored_job()
    again = B.fix_sequence(job["shots"])
    assert [s["camera"] for s in again] == [s["camera"] for s in job["shots"]]
    assert [s["action"] for s in again] == [s["action"] for s in job["shots"]]
    # правило «один план подряд нельзя» автору не указ
    beats = _beats(["слушает. План: лицо"] * len(SENTENCES))
    shots = _authored_job(beats)["shots"]
    assert {s["camera"] for s in shots if not s.get("screen")} == {"face"}, "кроме экрана телефона: его видно только деталью"


# --------------------------------------------------------------------------- #
# Проём: герой, двери, камера, луч
# --------------------------------------------------------------------------- #

def _at(job: dict, words_in: str) -> dict:
    return next(s for s in job["shots"] if words_in in s["text"])


def test_doors_held_open_until_they_snap_shut():
    job = _authored_job()
    perf = B.perform(job)
    close = next(e["t"] for e in job["events"] if e["kind"] == "doors_close")
    release = next(e["t"] for e in job["events"] if e["kind"] == "doors_release")
    assert release < close
    fps = perf["fps"]
    plate = _at(job, "край плиты")
    gaps = [perf["lights"][f]["doors"] for f in range(int(plate["t0"] * fps), int(close * fps))]
    assert min(gaps) == pytest.approx(B.DOORWAY["gap"], abs=0.005), "весь проём двери стоят открытыми"
    after = [perf["lights"][f]["doors"] for f in range(int((close + 0.25) * fps), perf["frames"])]
    assert max(after) < 1e-3, "сомкнулись и больше не открываются"
    assert perf["lights"][int((close + 0.25) * fps)]["reveal"] == 0.0, "закрылись — свет в шахту не идёт"
    frames = [f for f in range(perf["frames"]) if close <= f / fps <= close + 0.22]
    assert len(frames) <= 8, "схлопываются за долю секунды"
    # правая рука на створке до «отпустил»: левая в это время держит телефон
    f_hold = int((release - 0.4) * fps)
    assert perf["hands"]["r"][f_hold] and perf["hands"]["r"][f_hold][2] == "doors"
    assert perf["hands"]["l"][f_hold] and perf["hands"]["l"][f_hold][2] == "phone"
    assert perf["hands"]["r"][int((close + 0.1) * fps)] is None
    # испуг на схлопывании
    assert any(abs(t - close) < 0.3 for t, _a in perf["startles"])
    assert any(sg["clip"] == "Hit_A" and abs(sg["start"] - close) < 0.3 for sg in perf["track"])


def test_non_full_pry_does_not_reopen_after_close():
    """Бит старого вида: «отпустил створки» как pry_doors с закрытием внутри кадра — двери не открываются обратно."""
    script = {"title": "x", "sentences": [{"text": "Он отпустил створки, и они сомкнулись.", "role": ""}]}
    words = _words(script)
    job = B.build_job(script, words, float(words[-1]["end"]) + 3.0, gemini=False,
                      beats=[{"i": 0, "sentence": 0, "text": "", "spec": {"action": "раздвигает двери. План: сверху"}}])
    assert job["shots"][0]["action"] == "pry_doors"
    perf = B.perform(job)
    close = next(e["t"] for e in job["events"] if e["kind"] == "doors_close")
    assert max(perf["lights"][f]["doors"] for f in range(int((close + 0.25) * 30), perf["frames"])) < 1e-3


def test_doorway_cameras_see_slab_door_and_wall():
    job = _authored_job()
    perf = B.perform(job)
    cams = {c["n"]: c for c in perf["cameras"]}
    for text, kind in (("край плиты", "ots"), ("голая стена", "overhead")):
        s = next(x for x in perf["shots"] if text in x["text"])
        c = cams[s["n"]]
        assert c["kind"] == kind and s["at_door"]
        for name in ("slab", "floor_door", "wall"):
            assert B.cam_sees(c, B.DOORWAY[name], B.DOORWAY[name], 0.98), (text, name)
    # с позиции героя проём не перекрыт: он у края, точки смотрят мимо него
    assert B.DOORWAY["spot"][0] < -0.15 and B.DOORWAY["slab"][0] > 0.0


def test_beam_never_into_the_lens():
    job = _authored_job()
    perf = B.perform(job)
    cams = sorted(perf["cameras"], key=lambda c: c["t0"])
    fps = perf["fps"]
    for f in range(perf["frames"]):
        ph = perf["phone"][f]
        if not (ph["on"] and ph["light"] > 0.5):
            continue
        cam = next(c for c in reversed(cams) if c["t0"] * fps <= f + 1)
        hand = B.to_world(perf["root"][f], perf["heading"][f], (-0.07, 0.25, 0.57))
        to_b = np.subtract(ph["beam"], hand)
        to_c = np.subtract(cam["pos_a"], hand)
        cos = float(np.dot(to_b, to_c) / (np.linalg.norm(to_b) * np.linalg.norm(to_c)))
        # половина конуса фонарика 15°, с запасом на размытый край
        assert cos < math.cos(math.radians(24)), (f, cam["kind"], cos)


def test_flashlight_off_stays_off():
    job = _authored_job()
    perf = B.perform(job)
    s = _at(job, "выключил фонарик")
    frames = range(int((s["t0"] + 0.4) * 30), perf["frames"])
    assert max(perf["phone"][f]["light"] for f in frames) == 0.0, "выключил — луч больше сам не загорается"
    clock = _at(job, "23:36")
    assert clock["screen"] == "23:36" and clock["camera"] == "detail" and clock["action"] == "phone_out"

    # телефон убран, как только экран посмотрели: на финальных лицах он подбородок не закрывает
    script = _script()
    script["sentences"].append({"text": "Для проверки он стукнул в двери.", "role": ""})
    words = _words(script)
    job2 = B.build_job(script, words, float(words[-1]["end"]) + 2.0, gemini=False,
                       beats=_beats() + [{"i": 11, "sentence": 11, "text": "", "spec": {"action": "стучит кулаком. План: сверху"}}])
    perf2 = B.perform(job2)
    knock = job2["shots"][-1]
    assert knock["action"] == "knock" and perf2["phone"][perf2["frames"] - 3]["on"] == 0.0


def test_three_blows_get_a_real_knock_sound():
    script = {"title": "x", "sentences": [{"text": "Ему ответили, сверху. Три удара.", "role": ""}]}
    words = _words(script)
    job = B.build_job(script, words, float(words[-1]["end"]) + 2.0, gemini=False)
    assert any(e["kind"] == "knock_far" for e in job["events"])
    assert any(x["kind"] == "knock" for x in B.sfx_for(job, B.perform(job)))


def test_camera_follows_hero_on_frame_of_cut():
    """Склейка с перестановкой: первый кадр плана — уже на новом месте (пружина камеры не смотрит мимо героя)."""
    job = _authored_job()
    perf = B.perform(job)
    fps = perf["fps"]
    for s in perf["shots"]:
        f = int(round(s["t0"] * fps))
        if f + 1 < perf["frames"] and math.dist(perf["root"][f], perf["root"][f + 1]) > 0.1:
            pytest.fail(f"герой прыгнул на втором кадре плана {s['n']}")


# --------------------------------------------------------------------------- #
# Озвучка под темп
# --------------------------------------------------------------------------- #

def test_repeated_lines_found_by_exact_tail_only():
    words = _words(_script())
    reps = VF.repeated_lines(_script()["sentences"], words)
    assert len(reps) == 1 and reps[0]["words"] == 5
    a, b = reps[0]["first"], reps[0]["again"]
    norm = [VF._norm(w["word"]) for w in words]
    assert norm[a[0]:a[1] + 1] == norm[b[0]:b[1] + 1] == ["спасатели", "держитесь", "еще", "5", "минут"]
    assert a[1] < b[0]
    other = {"sentences": [{"text": "Он нажал кнопку лифта снова."}, {"text": "Он нажал кнопку снова."},
                           {"text": "Снова номер 23."}, {"text": "Снова номер 23."}]}
    assert VF.repeated_lines(other["sentences"], _words(other)) == [], "короткие и неполные повторы не склеиваем"
    assert VF.repeated_lines([{"text": "Совсем другой текст тут есть сегодня."}], _words(_script())) == []


def _speech_wav(path: Path, lines: list[list[tuple[str, float, float]]], pause: float, hz=(180.0, 240.0)) -> list[dict]:
    """Озвучка-подделка: слова — гармонические всплески с разным тоном по фразам, между фразами тишина ``pause``."""
    sr = VF.SR
    out, words, t, n = [], [], 0.08, 0
    out.append(np.zeros(int(t * sr), dtype=np.float32))
    for line in lines:
        for word, dur, gap in line:
            k = np.arange(int(dur * sr)) / sr
            f = hz[n % 2] + 37.0 * n
            out.append((0.35 * np.sin(2 * np.pi * f * k) * np.hanning(len(k))).astype(np.float32))
            words.append({"word": word, "start": round(t, 3), "end": round(t + dur, 3)})
            t += dur
            out.append(np.zeros(int(gap * sr), dtype=np.float32))
            t += gap
            n += 1
        out.append(np.zeros(int(pause * sr), dtype=np.float32))
        t += pause
    pcm = (np.concatenate(out) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm.tobytes())
    return words


def _fit_case(tmp_path: Path):
    a = [("Голос", 0.3, 0.05), ("ответил:", 0.4, 0.2), ("спасатели,", 0.5, 0.15), ("держитесь,", 0.5, 0.15), ("ещё", 0.2, 0.05),
         ("5", 0.2, 0.05), ("минут.", 0.4, 0.05)]
    b = [("«Как", 0.25, 0.05), ("зовут?»", 0.4, 0.05)]
    c = [("Спасатели,", 0.6, 0.25), ("держитесь,", 0.45, 0.1), ("ещё", 0.25, 0.08), ("5", 0.25, 0.08), ("минут.", 0.5, 0.05)]
    d = [("Запись,", 0.4, 0.15), ("понял", 0.3, 0.05), ("он.", 0.3, 0.05)]
    src = tmp_path / "src.wav"
    words = _speech_wav(src, [a, b, c, d], 0.9)
    sentences = [{"text": "Голос ответил: спасатели, держитесь, ещё 5 минут."}, {"text": "«Как зовут?»"},
                 {"text": "Спасатели, держитесь, ещё 5 минут."}, {"text": "Запись, понял он."}]
    return src, words, sentences


def test_fit_makes_repeat_identical_and_pauses_shorter(tmp_path):
    src, words, sentences = _fit_case(tmp_path)
    out = tmp_path / "fit.mp3"
    fit = VF.fit_narration(src, out, sentences, words)
    assert out.is_file() and fit["repeats"] == [{"first": [2, 6], "again": [9, 13], "words": 5}]
    w = fit["words"]
    assert [x["word"] for x in w] == [x["word"] for x in words], "слова и порядок те же"
    # те же слова, та же пауза: времена второй реплики — сдвиг первой
    shift = w[9]["start"] - w[2]["start"]
    for i in range(5):
        assert w[9 + i]["start"] - w[2 + i]["start"] == pytest.approx(shift, abs=0.004)
        assert w[9 + i]["end"] - w[2 + i]["end"] == pytest.approx(shift, abs=0.004)
    # и звук тот же (после кодека — почти до сэмпла)
    x = VF.decode(out)
    n = int((w[6]["end"] - w[2]["start"] - 0.1) * VF.SR)
    seg = x[int((w[2]["start"] + 0.05) * VF.SR):][:n]
    c0 = int((w[9]["start"] + 0.05) * VF.SR)
    best = max(float((seg * x[c0 + d:][:n]).sum() / np.sqrt((seg * seg).sum() * (x[c0 + d:][:n] ** 2).sum()))
               for d in range(-240, 240, 2))
    assert best > 0.97
    # пауза фразы короче исходной, порядок времени не сломан
    assert w[7]["start"] - w[6]["end"] < 0.5
    assert all(b["start"] >= a["start"] for a, b in zip(w, w[1:]))
    assert fit["speech_end"] < float(words[-1]["end"]) and fit["wps_after"] > fit["wps_before"]
    assert 1.0 <= fit["tempo"] <= VF.MAX_TEMPO
    assert abs(len(x) / VF.SR - fit["audio_sec"]) < 0.15, "длина файла совпадает с расчётом"


def test_tempo_stops_at_cap_and_target():
    slow = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(20)]
    assert VF.choose_tempo(slow) == VF.MAX_TEMPO, "не быстрее потолка"
    fast = [{"word": f"w{i}", "start": i * 0.2, "end": i * 0.2 + 0.15} for i in range(20)]
    assert VF.choose_tempo(fast) == 1.0, "быстрая озвучка не замедляется и не ускоряется"
    assert VF.choose_tempo([]) == 1.0


def test_fit_errors_are_explicit(tmp_path):
    with pytest.raises(VF.VoiceFitError):
        VF.fit_narration(tmp_path / "x.wav", tmp_path / "y.mp3", [], [])


# --------------------------------------------------------------------------- #
# Слова-ловушки
# --------------------------------------------------------------------------- #

def test_trap_words_need_word_boundary():
    assert B.rule_location("Сначала ищите выход", "cabin") == "cabin", "«ищите» не «щит»"
    assert B.rule_location("Он тащит сумку", "cabin") == "cabin", "«тащит» не «щит»"
    assert B.rule_location("Он открыл щит", "cabin") == "techfloor"
    assert B.rule_location("В электрощитовой темно", "cabin") == "techfloor"
    assert B.rule_location("Он проходит мимо", "cabin") == "cabin", "«проходит» не коридор"
    assert B.rule_location("Узкий проход вёл дальше", "cabin") == "corridor"
    assert B.rule_location("Тросы гудят", "cabin") == "shaft"
    names = lambda text: {x["item"] for x in B.show_items("techfloor", {"text": text})}  # noqa: E731
    assert "cabinet" not in names("Сначала ищите выход") and "cabinet" in names("На стене щит")
    assert "doors" not in names("Он растворился в темноте")
    assert "hatch" not in names("Злюка ушла") and "hatch" in {x["item"] for x in B.show_items("cabin", {"text": "Открыл люк"})}
    pick = lambda text: B.rule_choice({"text": text, "role": "problem"}, [], dark=False)["action"]  # noqa: E731
    assert pick("Он бежал и держал сумку") != "press_button" and pick("Он жал кнопку") == "press_button"
    assert pick("Над ним висел провод") != "sit_corner" and pick("Он сел на пол") == "sit_corner"


# --------------------------------------------------------------------------- #
# Живой путь: перепланирование и голос клипа
# --------------------------------------------------------------------------- #

def test_key_changes_with_plan_and_keeps_narration():
    ep = {"id": 5, "script": _script(), "beats": _beats(), "options": {}}
    base = S3D.blender_key(ep, {"asset_id": 7})
    assert base == S3D.blender_key(ep, {"asset_id": 7})
    changed = _beats()
    changed[2] = {**changed[2], "spec": {"action": "светит, страх. План: лицо"}}
    assert base != S3D.blender_key({**ep, "beats": changed}, {"asset_id": 7}), "правка плана — новое задание"
    assert base != S3D.blender_key({**ep, "options": {"voice_fit": False}}, {"asset_id": 7})


def test_new_job_cancels_old_queued_of_same_episode(env):  # noqa: F811
    _client, store, _tmp = env
    old = RQ.enqueue(store, {"v": 1}, key="old-key", episode_id=9, title="старое")
    other = RQ.enqueue(store, {"v": 1}, key="other", episode_id=10, title="чужое")
    assert RQ.cancel_stale_queued(store, 9, "new-key") == [old["id"]]
    assert RQ.get(store, old["id"])["status"] == "cancelled" and RQ.get(store, other["id"])["status"] == "queued"
    running = RQ.enqueue(store, {"v": 1}, key="run", episode_id=11)
    RQ.claim(store, "pc")   # старейшее: other
    assert RQ.cancel_stale_queued(store, 11, "run") == [], "своё задание с тем же ключом не трогаем"
    assert RQ.get(store, running["id"])["status"] == "queued"


def test_episode_clip_uses_fitted_voice(env, tmp_path, monkeypatch):  # noqa: F811
    from app.aivideo_gen import _asset_path

    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=LIFT,
                  options={"engine": "3d", "renderer": "blender"}, run=True)
    assert ep["status"] == "assembling", (ep["error"], ep.get("notes"))
    eid = int(ep["id"])
    old = RQ.list_jobs(store, episode_id=eid)[0]
    assert not S.get_episode(store, eid)["timeline"].get("voice_fit"), "у сухого прогона озвучка как есть"
    full = S.get_episode(store, eid)
    src = S.narration_ref(store, full)
    src_path = Path(_asset_path(store, src))
    words = _words()

    def fake_fit(path, out, sentences, words_in, **_k):   # анализ звука отдельно: тут только путь через сервис
        Path(out).write_bytes(src_path.read_bytes())
        return {"v": VF.VERSION, "words": [{**w, "start": round(w["start"] * 0.9, 3), "end": round(w["end"] * 0.9, 3)} for w in words_in],
                "tempo": 1.1, "audio_sec": 12.0, "source_sec": 13.0, "repeats": [], "wps_before": 2.0, "wps_after": 2.3,
                "speech_end": float(words_in[-1]["end"]) * 0.9}

    monkeypatch.setattr(VF, "fit_narration", fake_fit)
    notes: list[str] = []
    fit = S3D.make_fit(store, full, src, src_path, words, notes)
    assert fit and not notes and fit["source"] == int(src["asset_id"]) and fit["asset_id"] != int(src["asset_id"])
    full = S.get_episode(store, eid)
    assert S3D.stored_fit(store, full, src)["asset_id"] == fit["asset_id"]
    assert S3D.stored_fit(store, full, {**src, "asset_id": 999999}) is None, "другая запись — заново"
    # новая режиссура: старое задание снято, новое на новых временах и новой озвучке
    RQ.cancel(store, old["id"])
    S3D_key = S3D.blender_key(full, src)
    assert RQ.find(store, S3D_key) is None
    X.advance(store, eid)
    job = RQ.list_jobs(store, episode_id=eid)[0]
    assert job["id"] != old["id"] and job["status"] == "queued"
    payload = RQ.find(store, job["key"], payload=True)["payload"]
    assert payload["words"][-1]["end"] == pytest.approx(fit["speech_end"], abs=0.01)
    X.advance(store, eid)
    assert RQ.list_jobs(store, episode_id=eid)[0]["id"] == job["id"], "второй проход — то же задание"
    got = RQ.claim(store, "pc")
    mp4 = tmp_path / "series.mp4"
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=#203040:s=108x192:d={payload['duration']}:r=10",
                    "-pix_fmt", "yuv420p", str(mp4)], check=True)
    RQ.complete(store, got["id"], "pc", mp4, {"seconds": 1.0, "frames": 10})
    ep = X.advance(store, eid)
    assert ep["status"] == "done", (ep["error"], ep.get("notes"))
    assert ep["timeline"]["voice_fit"]["asset_id"] == fit["asset_id"], "пересборка найдёт тот же файл"
    layers = store.get_clip_plan(ep["clip_id"])["render_settings"]["layers"]
    voice = next(lay for lay in layers if lay["id"].endswith("narr"))
    assert str(voice["props"]["asset_id"]) == str(fit["asset_id"]), "в клипе голос под темп, а не исходная запись"
