"""Объяснялки (app/explainer*.py): тема → сценарий → биты → позы из библиотеки → карточка → холст-клип
как пилот 887. Никаких настоящих платных вызовов и сети: Gemini, Whisper и «платный» провайдер — подделки."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import aivideo as V
from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app import explainer as E
from app import explainer_gen as X
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg

SCRIPT = {
    "title": "3 ошибки сна",
    "hook": "Спишь восемь часов, а встаёшь разбитым?",
    "intro": "Есть три ошибки, которые крадут твой сон.",
    "points": [
        {"title": "Ошибка номер один: телефон в кровати.",
         "body": "Яркий экран говорит мозгу, что сейчас день. Убери телефон за полчаса до сна."},
        {"title": "Ошибка номер два: разное время подъёма.",
         "body": "В будни в семь, в выходные в двенадцать. Вставай в одно время каждый день."},
        {"title": "Ошибка номер три: кофе после обеда.",
         "body": "Кофеин держится в теле часами. Последняя кружка — до двух часов дня."},
    ],
    "cta": "Исправь хотя бы одну ошибку, и через неделю почувствуешь разницу. Подпишись!",
}

# Новые позы, которые «Gemini» предлагает по словам бита (ключ → описание).
NEW_POSES = {
    "phone": {"key": "phone", "title": "с телефоном в кровати",
              "description": "lying in a small bed, face lit blue by a glowing smartphone held in both wings",
              "tags": ["phone", "bed", "night", "телефон", "кровать", "экран"], "emotion": "absorbed"},
    "alarm": {"key": "alarm", "title": "будильник",
              "description": "startled, jumping up from a small bed, a ringing red alarm clock next to it",
              "tags": ["alarm", "morning", "будильник", "утро", "подъём"], "emotion": "startled"},
    "coffee": {"key": "coffee", "title": "пьёт кофе",
               "description": "happily sipping from a huge steaming coffee mug held in both wings",
               "tags": ["coffee", "mug", "кофе", "кружка", "кофеин"], "emotion": "happy"},
}
KEYWORDS = {"телефон": "phone", "экран": "phone", "будни": "alarm", "подъём": "alarm", "кофе": "coffee",
            "кружка": "coffee"}


class FakeGemini:
    """Подделка Gemini: сценарий — SCRIPT; план — позы по ключевым словам бита (как сделал бы Gemini),
    библиотеку смотрит только для чисел (пальцы). Новые позы предлагает КАЖДЫЙ раз — переиспользование
    должно обеспечить сам сервис (similar_pose)."""

    def __init__(self, script: dict | None = None):
        self.script = script or SCRIPT
        self.calls: list[str] = []

    def generate_content(self, model, payload):
        props = payload["generationConfig"]["responseSchema"]["properties"]
        context = json.loads(payload["contents"][0]["parts"][1]["text"])
        if "points" in props:
            self.calls.append("script")
            return self._wrap(self.script)
        self.calls.append("plan")
        library = context["library"]
        beats, new = [], {}
        for b in context["beats"]:
            ans = {"i": b["i"], "pose_id": 0}
            text = b["text"].lower()
            if b.get("point_start"):
                hit = next((p for p in library if p.get("number_shown") == b["point_start"]), None)
                ans["pose_id"] = hit["id"] if hit else 0
                if not hit:
                    key = f"num{b['point_start']}"
                    new[key] = {"key": key, "title": f"показывает {b['point_start']}",
                                "description": f"serious face, holding up one wing showing {b['point_start']} "
                                               "raised feather-fingers",
                                "tags": ["count", "number", "номер"], "number_shown": b["point_start"]}
                    ans["new_pose_key"] = key
            else:
                key = next((v for k, v in KEYWORDS.items() if k in text), None)
                if key:
                    new[key] = NEW_POSES[key]
                    ans["new_pose_key"] = key
                elif b["candidates"]:
                    ans["pose_id"] = b["candidates"][0]
                else:
                    ans["pose_id"] = library[b["i"] % len(library)]["id"]
            if "семь" in text:
                ans["chip"], ans["chip_word"] = "07:00", "семь"
            ans["emphasis"] = [w.strip(",.?!") for w in b["text"].split() if len(w) > 6][:1]
            beats.append(ans)
        return self._wrap({"beats": beats, "new_poses": list(new.values())})

    @staticmethod
    def _wrap(data):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(data, ensure_ascii=False)}]}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    monkeypatch.setattr(settings, "aivideo_enabled", False)
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 3000.0)
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 2500.0)
    monkeypatch.setattr(settings, "explainer_video_cap_rub", 150.0)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    monkeypatch.setattr(PR, "MEASURED_IMAGE_PRICES", {})  # прайс ×2: 6 ₽ за позу
    # Whisper в тестах не грузим: сухой прогон и так «по длине текста»; платный путь — подделка.
    monkeypatch.setattr(X, "transcribe_words", lambda path: (_ for _ in ()).throw(RuntimeError("нет whisper")))
    client, store = _client(tmp_path, monkeypatch)
    return client, store, tmp_path


def _image(store, tmp: Path, name: str, color: str = "0x2a1b47") -> int:
    path = tmp / f"{name}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=72x128:d=1",
                    "-frames:v", "1", str(path)], check=True)
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / path.name
    dest.write_bytes(path.read_bytes())
    return int(store.create_montage_asset(kind="image", label=name, description="", tags="", file_path=str(dest),
                                          original_filename=path.name, mime_type="image/jpeg",
                                          size_bytes=dest.stat().st_size, **probe_asset(dest))["id"])


def _music(store, tmp: Path) -> int:
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    music = settings.audio_dir / "expl-music.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=70",
                    str(music)], check=True)
    return int(store.create_audio_track("фон", music, duration_sec=70)["id"])


def _setup(store, tmp: Path, provider: str = "none", voice: dict | None = None, music: bool = True) -> dict:
    pid = V.create_ai_project(store, "Ух объясняет", provider=provider)["project"]["id"]
    mascot = E.save_mascot(store, pid, name="Ух", identity_card="a chubby round midnight-blue owl in a grey hoodie",
                           persona="сова-полуночник", bg_color="#2A1B47",
                           voice=voice or {"provider": "gemini", "voice": "Puck"},
                           music_track_id=_music(store, tmp) if music else None)
    E.set_sheet(store, mascot["id"], _image(store, tmp, "sheet"))
    # Готовые позы: числа 1–3 пальцами, указывает, машет, палец вверх.
    lib = [("one", "serious face, holding up one wing showing one raised feather-finger", ["one", "один"], 1),
           ("two", "serious face, holding up one wing showing two raised feather-fingers", ["two", "два"], 2),
           ("three", "serious face, raising one wing with exactly three feather-fingers", ["three", "три"], 3),
           ("point", "confident smirk, pointing one wing-finger at the viewer",
            ["point", "you", "ты", "давай", "внимание", "ошибки"], None),
           ("wave", "cheerfully waving goodbye", ["wave", "подпишись", "пока", "разницу"], None),
           ("thumbs", "big happy smile, thumbs-up", ["thumbs up", "отлично", "исправь", "сон"], None),
           ("sleepy", "very sleepy, yawning, hugging a pillow", ["sleepy", "спишь", "разбитым", "часов"], None)]
    for slug, desc, tags, num in lib:
        E.save_pose(store, mascot["id"], description=desc, title=slug, tags=tags, number_shown=num, origin="pilot",
                    asset_id=_image(store, tmp, f"pose_{slug}", "0x3a2b57"), slug=slug)
    return {"project_id": pid, "mascot_id": mascot["id"]}


def _layers(store, clip_id):
    return store.get_clip_plan(clip_id)["render_settings"]["layers"]


# --------------------------------------------------------------------------- #
# Сухой прогон: тема → готовый холст-клип со слоями пилота
# --------------------------------------------------------------------------- #


def test_dry_run_end_to_end_builds_clip_like_pilot(env, monkeypatch):
    _client_, store, tmp = env
    from app.settings import settings

    monkeypatch.setattr(settings, "auto_mix", True)  # сборка зовёт автосведение (в тестах выключено)
    # Пак freesound в тестах не скачан: считаем звуки пака на месте (рендера в этом тесте нет).
    import app.motion_render as MR

    monkeypatch.setattr(X, "_sfx_ok", lambda ref: True)
    monkeypatch.setattr(MR, "pack_refs_available", lambda: False)
    ids = _setup(store, tmp)
    fake = FakeGemini()
    video = X.create(store, ids["mascot_id"], "почему ты не высыпаешься", 45, "дружелюбно", run=True, client=fake)
    assert fake.calls == ["script", "plan"]
    assert video["status"] == "done", (video["error"], video.get("notes"))
    assert video["script"]["points"] == 3 and video["script"]["text"].startswith("Спишь восемь часов")
    # Новые позы «Gemini» (телефон, будильник, кофе) нарисованы сухим прогоном — в библиотеке.
    poses = E.list_poses(store, ids["mascot_id"])
    assert {p["title"] for p in poses if p["origin"] == "generated"} == {"с телефоном в кровати", "будильник",
                                                                         "пьёт кофе"}
    assert all(p["ready"] for p in poses)
    assert G.ledger(store, ids["project_id"]) == []  # ни рубля

    layers = _layers(store, video["clip_id"])
    by_type: dict[str, list] = {}
    for layer in layers:
        by_type.setdefault(layer["type"], []).append(layer)
    images = sorted(by_type["image"], key=lambda l: l["start"])
    total = store.get_clip_plan(video["clip_id"])["render_settings"]
    beats = video["timeline"]["beats"]
    assert len(images) == len(beats) >= 12
    # Позы встык от 0 до конца, каждая 0.5–3.6 с, анимация входа чередуется pop / zoom.
    assert images[0]["start"] == 0
    for a, b in zip(images, images[1:]):
        assert abs(a["end"] - b["start"]) < 1e-6
    durations = [l["end"] - l["start"] for l in images]
    assert max(durations[:-1]) <= X.REAL_SPLIT_SEC + 0.01 and min(durations) >= 0.35
    assert images[0]["in"]["type"] == "pop" and images[1]["in"]["type"] == "zoom"
    assert abs(images[-1]["end"] - video["timeline"]["duration"]) < 1e-6
    assert all(l["props"]["w"] == 1080 for l in images)
    # Соседние позы разные (или тот же кадр с наездом).
    for a, b in zip(beats, beats[1:]):
        assert a["pose_id"] != b["pose_id"] or b.get("variant") == "punch"
    # Вжух на каждой склейке, «динь» на каждой плашке.
    audio = {l["id"]: l for l in by_type["audio"]}
    whooshes = [l for k, l in audio.items() if k.startswith("wh")]
    assert len(whooshes) == len(images) - 1
    assert {l["props"]["asset_id"] for l in whooshes} == set(X.CUT_SFX)
    texts = {l["props"]["text"]: l for l in by_type["text"]}
    assert {"#1", "#2", "#3"} <= set(texts)
    assert sum(1 for k in audio if k.startswith("ding")) == 3
    # Плашка #1 — на бите «Ошибка номер один…», цвет акцента и обводка маскота.
    b1 = next(b for b in beats if b.get("badge") == "#1")
    assert b1["text"].startswith("Ошибка номер один") and texts["#1"]["start"] == b1["start"]
    assert texts["#1"]["props"]["color"] == "#ffd447" and texts["#1"]["props"]["size"] == 170
    # Чип «07:00» на слове «семь», с плашкой цвета чипа.
    chip = texts["07:00"]
    assert chip["props"]["bg"]["color"] == "#7a6ff0"
    # Голос и субтитры: слой captions hormozi капсом, слова с эмфазой.
    assert audio["narr"]["start"] == 0 and audio["narr"]["props"]["volume"] == 1.0
    caps = by_type["captions"][0]
    assert caps["props"]["style"] == "hormozi" and caps["props"]["uppercase"] is True
    assert caps["props"]["emphColor"] == "#ffd447" and caps["y"] == 0.84
    subs = store.get_clip_plan_subtitles(video["clip_id"])
    assert sum(1 for w in subs["words"] if w.get("emph")) >= 5
    # Музыка маскота под голосом тише; фон холста — цвет маскота.
    assert total["music_on"] and total["track_id"] == E.get_mascot(store, ids["mascot_id"])["music_track_id"]
    # Сборка зовёт автосведение (AUTO_MIX): громкость музыки — по замеру голоса; озвучка здесь —
    # сплошной тон без пауз, поэтому ducking по фразам не нужен (музыка просто тише).
    mix = total.get("mix") or {}
    if mix.get("auto"):
        assert mix["music_db"] is not None and mix["sidechain"] is False and mix["target_lufs"] == -14.0
    else:
        assert total["audio"]["music_duck"] and total["audio"]["music_duck"][0]["gain_db"] == X.MUSIC_DUCK_DB
    assert mix.get("auto"), "автосведение после сборки объяснялки"
    assert total["backdrop"]["colors"][0] == "#2a1b47"
    # Позы помечены «использованы».
    assert any(p["uses"] for p in E.list_poses(store, ids["mascot_id"]))


def test_without_sfx_pack_no_synthetic_sounds(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False)
    video = X.create(store, ids["mascot_id"], "сон", 45, script=SCRIPT, run=True, client=None)
    assert video["status"] == "done", video["error"]
    layers = _layers(store, video["clip_id"])
    if not X._sfx_ok(X.CUT_SFX[0]):
        assert not [l for l in layers if l["type"] == "audio" and str(l["props"]["asset_id"]).startswith("sfx:")]
        assert any("freesound" in n for n in video["notes"])
    # Без Gemini позы — по тегам библиотеки, без новых; плашки — по пунктам сценария.
    assert any("Gemini недоступен" in n for n in video["notes"])
    assert all(p["origin"] == "pilot" for p in E.list_poses(store, ids["mascot_id"]))
    beats = video["timeline"]["beats"]
    pose_of = {b["badge"]: b["pose_id"] for b in beats if b.get("badge")}
    numbers = {p["id"]: p["number_shown"] for p in E.list_poses(store, ids["mascot_id"])}
    assert [numbers[pose_of[f"#{n}"]] for n in (1, 2, 3)] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# Переиспользование поз
# --------------------------------------------------------------------------- #


def test_second_video_reuses_poses_and_generates_none(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp)
    fake = FakeGemini()
    first = X.create(store, ids["mascot_id"], "3 ошибки сна", 45, run=True, client=fake)
    assert first["status"] == "done"
    before = {p["id"] for p in E.list_poses(store, ids["mascot_id"])}
    jobs_before = len(G.list_jobs(store, ids["project_id"], limit=1000))

    # Второй ролик: «Gemini» снова описывает те же новые позы — сервис берёт готовые.
    second = X.create(store, ids["mascot_id"], "как проснуться бодрым", 45, run=True, client=FakeGemini())
    assert second["status"] == "done", second["error"]
    assert {p["id"] for p in E.list_poses(store, ids["mascot_id"])} == before
    assert any("беру готовую" in n for n in second["notes"])
    image_jobs = [j for j in G.list_jobs(store, ids["project_id"], limit=1000)[: -jobs_before or None]
                  if j["kind"] == "image"]
    assert image_jobs == []
    assert set(b["pose_id"] for b in second["timeline"]["beats"]) <= before


def test_similar_pose_respects_numbers_and_tags():
    lib = [{"id": 1, "description": "serious face, holding up one wing showing two raised feather-fingers",
            "tags": ["two", "два"], "title": "два", "number_shown": 2},
           {"id": 2, "description": "happily sipping from a huge steaming coffee mug held in both wings",
            "tags": ["coffee", "кофе", "кружка"], "title": "кофе", "number_shown": None}]
    hit, _ = E.similar_pose(lib, "holding up one wing showing three raised feather-fingers", ["three"])
    assert hit is None  # три пальца ≠ два
    hit, sim = E.similar_pose(lib, "happily sipping coffee from a huge mug in both wings", ["кофе", "coffee"])
    assert hit and hit["id"] == 2 and sim >= E.SIMILAR_MIN
    hit, _ = E.similar_pose(lib, "angry, stomping, holding a broken umbrella", ["злой", "зонт"])
    assert hit is None
    ranked = E.candidates(lib, "Последняя кружка кофе — до двух часов дня")
    assert ranked[0][0]["id"] == 2


def test_align_maps_digits_and_splits_long_beats():
    beats = [{"i": 0, "text": "Спишь восемь часов, а встаёшь разбитым?", "pose_id": 1},
             {"i": 1, "text": "В будни в семь, в выходные в двенадцать, и организм живёт как в вечном перелёте.",
              "pose_id": 2, "chip": {"text": "07:00", "word": "семь"}},
             {"i": 2, "text": "Подпишись!", "pose_id": 3}]
    spoken = "Спишь 8 часов, а встаешь разбитым? В будни в 7, в выходные в 12, и организм живет как в вечном " \
             "перелете. Подпишись!".split()
    words, t = [], 0.0
    for w in spoken:
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += 0.38
    placed = X.place(beats, words, t + 0.8)
    assert placed[0]["start"] == 0
    second = next(p for p in placed if p["i"] == 1)
    assert words[second["w0"]]["word"] == "В" and abs(second["start"] - words[6]["start"]) < 1e-6
    # «В будни… перелёте» ≈ 6 с — делится тем же кадром с наездом.
    halves = [p for p in placed if p["i"] == 1]
    assert len(halves) == 2 and halves[1]["variant"] == "punch" and halves[1]["pose_id"] == 2
    marks = X.emphasis_marks(placed, words)
    assert any(words[k]["word"].startswith("7") for k in marks)  # чип «семь» → слово «7,»


# --------------------------------------------------------------------------- #
# Деньги: карточка, одобрение, потолки
# --------------------------------------------------------------------------- #


class FakePaid:
    name = "polza"
    paid = True

    def __init__(self):
        self.posts = 0

    def submit(self, params, refs):
        self.posts += 1
        return f"req-{self.posts}"

    def check(self, remote_id):
        return {"status": "completed", "url": "https://example.invalid/x.jpg", "cost_rub": 6.0}

    def fetch(self, result, dest, params):
        return PR.DryRunImages().fetch(result, dest, params)


@pytest.fixture
def paid(env, monkeypatch):
    fake = FakePaid()
    monkeypatch.setattr(PR, "get_image_provider", lambda name: fake if name == "polza" else PR.DryRunImages())
    from app.dubbing import DryRunTTS

    class FreeGeminiTTS(DryRunTTS):  # «Gemini TTS» без сети: бесплатный тон
        name = "gemini"

    real = PR.get_tts
    monkeypatch.setattr(PR, "get_tts", lambda name, *a, **k: FreeGeminiTTS() if name == "gemini" else real(name, *a, **k))
    return env, fake


def test_new_poses_need_one_owner_card_and_respect_caps(paid, monkeypatch):
    (_client_, store, tmp), fake = paid
    ids = _setup(store, tmp, provider="polza")
    video = X.create(store, ids["mascot_id"], "сон", 45, run=True, client=FakeGemini())
    assert video["status"] == "awaiting_approval" and fake.posts == 0
    # Бесплатная озвучка (Gemini TTS) уже сделана, в карточке — только новые позы (3 × 6 ₽).
    assert E.narration_ref(store, E.get_video(store, video["id"])) is not None
    card = A.get_approval(store, video["approval_id"])
    assert card["status"] == "pending" and card["payload"]["amount_rub"] == pytest.approx(18.0)
    assert card["payload"]["group"] == f"expl_video:{video['id']}"
    assert "новых поз 3" in card["question"] and "поз из библиотеки" in card["question"]
    est = X.estimate(store, video["id"])
    assert est["new_poses"] == 3 and est["narration_rub"] == 0 and est["reused_poses"] >= 5

    # Ничего не идёт без «Разрешить».
    G.run_pending(store)
    assert fake.posts == 0
    assert X.advance(store, video["id"])["status"] == "awaiting_approval"

    # Одобрено, но платная генерация выключена — держится, ролик ждёт (не падает).
    A.decide(store, video["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == 0
    assert X.advance(store, video["id"])["status"] == "generating"

    monkeypatch.setattr(settings, "aivideo_enabled", True)
    G.run_approved(store, video["approval_id"], confirm=True)
    assert fake.posts == 3
    done = X.advance(store, video["id"])
    assert done["status"] == "done" and done["clip_id"], done["error"]
    led = G.ledger(store, ids["project_id"])
    assert len(led) == 3 and sum(l["amount_rub"] for l in led) == pytest.approx(18.0)

    # Потолок одного ролика ниже суммы — ролик не просит карточку.
    monkeypatch.setattr(settings, "explainer_video_cap_rub", 5.0)
    script = dict(SCRIPT, points=SCRIPT["points"] + [{"title": "Ошибка номер четыре: душная спальня.",
                                                      "body": "Проветри комнату перед сном."}])
    v2 = X.create(store, ids["mascot_id"], "сон 2", 45, run=True, client=FakeGemini(script))
    assert v2["status"] == "failed" and "потолка одного ролика" in v2["error"]
    assert fake.posts == 3


def test_reject_cancels_video_and_paid_voice_is_in_the_card(paid, monkeypatch):
    (_client_, store, tmp), fake = paid
    ids = _setup(store, tmp, provider="polza", voice={"provider": "polza", "model": "elevenlabs/eleven-v4",
                                                       "voice": "liam"})
    video = X.create(store, ids["mascot_id"], "сон", 45, run=True, client=FakeGemini())
    card = A.get_approval(store, video["approval_id"])
    est = X.estimate(store, video["id"])
    # ElevenLabs по замеру ≈ 0.00466 ₽/символ: озвучка ~3 ₽ + 3 позы × 6 ₽.
    assert 1.0 < est["narration_rub"] < 5.0 and est["new_poses"] == 3
    assert card["payload"]["amount_rub"] == pytest.approx(est["total_rub"])
    assert "озвучка" in card["question"] and "liam" in card["question"]
    A.decide(store, video["approval_id"], "reject", comment="дорого", actor="ui")
    out = X.advance(store, video["id"])
    assert out["status"] == "cancelled" and "rejected" in out["error"]
    assert fake.posts == 0
    assert {j["status"] for j in G.list_jobs(store, approval_id=video["approval_id"])} == {"cancelled"}
    # Перегенерация одной позы — своя маленькая карточка (через общий aiv_regenerate тоже).
    pose = E.list_poses(store, ids["mascot_id"])[0]
    req = E.request_pose(store, pose["id"], regenerate=True)
    assert req["status"] == "awaiting_approval" and req["estimate"]["total_rub"] == pytest.approx(6.0)
    ref = E.get_pose(store, pose["id"])
    req2 = G.regenerate(store, ref_id=ref["ref_id"])
    assert req2["status"] == "awaiting_approval"


# --------------------------------------------------------------------------- #
# Правки агентом, импорт пилота, модуль, REST
# --------------------------------------------------------------------------- #


def test_agent_script_and_beat_override(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp)
    video = E.create_video(store, ids["mascot_id"], "сон", 45)
    X.set_script(store, video["id"], SCRIPT)
    X.plan_beats(store, video["id"], client=None)  # без Gemini — по тегам
    beats = E.get_video(store, video["id"])["beats"]
    assert beats[0]["section"] == "hook" and [b["badge"] for b in beats if b.get("badge")] == ["#1", "#2", "#3"]
    assert all(1 <= len(b["text"]) / X.CPS <= X.BEAT_MAX_SEC + 0.6 for b in beats)
    out = X.update_beats(store, video["id"], [{"i": 1, "new_pose": {"description": "juggling three tiny moons",
                                                                    "title": "жонглирует лунами",
                                                                    "tags": ["луна", "жонглирует"]}},
                                              {"i": 2, "chip": "8 ч", "chip_word": "восемь"}])
    assert out["status"] == "planned" and out["beats"][1]["pose_new"] and out["beats"][2]["chip"]["text"] == "8 ч"
    est = X.estimate(store, video["id"])
    assert est["new_poses"] == 1 and est["total_rub"] == 0  # сухой прогон
    final = X.advance(store, video["id"])
    assert final["status"] == "done", final["error"]
    with pytest.raises(E.ExplError):
        X.update_beats(store, video["id"], [{"i": 999, "pose_id": 1}])


def test_import_pilot_is_idempotent(env):
    _client_, store, tmp = env
    from app.explainer_pilot import POSES, SHEET_FILE

    folder = tmp / "pilot"
    folder.mkdir()
    for name in [SHEET_FILE] + [p["file"] for p in POSES] + ["pose_3.jpg"]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2a1b47:s=48x86:d=1",
                        "-frames:v", "1", str(folder / name)], check=True)
    out = E.import_pilot(store, None, folder)
    assert len(out["added"]) == 18 and not out["notes"]
    mascot = E.get_mascot(store, out["mascot_id"])
    assert mascot["name"] == "Ух" and mascot["sheet"] and mascot["voice"]["voice"] == "liam"
    assert V.get_style(store, out["project_id"])["provider"] == "polza"
    poses = E.list_poses(store, out["mascot_id"])
    assert len(poses) == 18 and all(p["ready"] and p["origin"] == "pilot" for p in poses)
    assert sorted(p["number_shown"] for p in poses if p["number_shown"]) == [1, 2, 3]
    again = E.import_pilot(store, None, folder)
    assert again["added"] == [] and len(again["skipped"]) == 18 and again["project_id"] == out["project_id"]
    assert len(E.list_poses(store, out["mascot_id"])) == 18


def test_module_tools_and_rest(env):
    client, store, tmp = env
    from app import agent_tools, chat_tools, modules
    from app.explainer_tools import TOOL_NAMES

    names = {t.name for t in agent_tools._project_tools()}
    assert set(TOOL_NAMES) <= names
    assert all(modules.module_of_tool(n) == "explainer" for n in TOOL_NAMES)
    assert set(TOOL_NAMES) <= set(chat_tools.group_tools("explainer"))
    assert "ai_video" in modules.MODULES_BY_ID["explainer"].depends

    ids = _setup(store, tmp)
    r = client.get(f"/api/projects/{ids['project_id']}/explainer")
    assert r.status_code == 200 and r.json()["mascots"][0]["name"] == "Ух"
    assert len(r.json()["mascots"][0]["poses"]) == 7
    r = client.patch(f"/api/explainer/mascots/{ids['mascot_id']}", json={"accent_color": "#ff0000"})
    assert r.status_code == 200 and r.json()["accent_color"] == "#ff0000"
    img = tmp / "up.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-frames:v", "1",
                    str(img)], check=True)
    with img.open("rb") as fh:
        r = client.post(f"/api/explainer/mascots/{ids['mascot_id']}/poses/upload",
                        files={"file": ("up.png", fh, "image/png")},
                        data={"description": "dancing", "title": "танцует", "tags": "танец, dance"})
    assert r.status_code == 200 and r.json()["ready"] and r.json()["tags"] == ["танец", "dance"]
    r = client.post(f"/api/explainer/mascots/{ids['mascot_id']}/videos", json={"topic": "сон", "duration": 90})
    assert r.status_code == 400
    r = client.post(f"/api/explainer/mascots/{ids['mascot_id']}/videos", json={"topic": "сон", "duration": 45})
    assert r.status_code == 200 and r.json()["status"] == "new"
    vid = r.json()["id"]
    r = client.put(f"/api/explainer/videos/{vid}/script", json=SCRIPT)
    assert r.status_code == 200 and r.json()["status"] == "scripted"
    r = client.post(f"/api/explainer/videos/{vid}/continue", json={})
    assert r.status_code == 200 and r.json()["status"] == "done"

    # MCP: инструменты в списке; выключенный модуль их прячет.
    from app.agent_tools import ToolContext, run_tool

    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "explainer_status", {"video_id": vid})
    assert ok and out["status"] == "done" and out["clip_id"] and out["beats"]
    out, ok = run_tool(ctx, "explainer_create", {"mascot_id": ids["mascot_id"], "topic": "кофе", "duration": 20})
    assert not ok and "30–60" in out["error"]
    modules.set_modules(store, {"explainer": False})
    try:
        assert "explainer_create" not in {t.name for t in agent_tools.list_tools(True, store)}
        assert client.get(f"/api/projects/{ids['project_id']}/explainer").status_code == modules.HTTP_STATUS
    finally:
        modules.set_modules(store, {"explainer": True})


# --------------------------------------------------------------------------- #
# Настоящий рендер компоновщиком (короткий сухой прогон, заглушки поз)
# --------------------------------------------------------------------------- #


def test_dry_run_render_with_real_compositor(env):
    _client_, store, tmp = env
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    ids = _setup(store, tmp, music=False)
    short = {"hook": "Спишь, а встаёшь разбитым?", "points": [
        {"title": "Совет номер один: убери телефон.", "body": "Экран будит мозг."},
        {"title": "Совет номер два: вставай в семь.", "body": "Каждый день."}],
        "cta": "Подпишись!"}
    video = E.create_video(store, ids["mascot_id"], "сон коротко", 30)
    X.set_script(store, video["id"], short)
    done = X.advance(store, video["id"])
    assert done["status"] == "done", done["error"]
    from app.dubbing import probe_duration
    from app.render import ClipRenderService

    preset = store.create_ffmpeg_preset("expl test", output_width=144, output_height=256, fps=12)
    clip = ClipRenderService(store).render_clip_plan(done["clip_id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    path = Path(clip["output_path"])
    assert path.is_file() and path.stat().st_size > 5_000
    assert abs(probe_duration(path) - done["timeline"]["duration"]) < 1.0
