"""Истории (app/stories*.py): завязка → сценарий с кадрами на каждую фразу → сцены из библиотеки мира →
ОДНА карточка → холст-клип (камера, звуки действий, субтитры по слову, музыка). Никаких настоящих
платных вызовов и сети: Gemini, Whisper, TTS и «платный» провайдер картинок — подделки."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from app import aivideo as V
from app import aivideo_gen as G
from app import aivideo_providers as PR
from app import autopilot as A
from app import stories as S
from app import stories_gen as X
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg

BOB = "a small round-headed toy prisoner, huge perfectly round beige head, dot eyes, orange jumpsuit #ff8a1f"
GENA = "a chubby round-headed toy prison guard, navy uniform #23305e, tiny peaked cap, big moustache"


def _shot(loc, chars, action, tags, camera="", sfx="", accent=""):
    return {"location": loc, "characters": chars, "action": action, "tags": tags, "camera": camera, "sfx": sfx,
            "accent": accent}


# «Побег из тюрьмы» ~50 с: на каждую фразу 1–2 кадра, звуки на действиях, акценты.
STORY = {
    "title": "Побег с ложкой",
    "logline": "Заключённый год копает ложкой, а дверь была открыта",
    "new_characters": [{"key": "bob", "name": "Боб", "identity_card": BOB, "persona": "упрямый мечтатель"},
                       {"key": "gena", "name": "Гена", "identity_card": GENA, "persona": "добрый соня"}],
    "new_locations": [
        {"key": "cell", "name": "Тюремная камера", "description": "a cosy tiny prison cell, bunk bed, brick wall"},
        {"key": "corridor", "name": "Коридор", "description": "a prison corridor with a guard desk and a lamp"},
        {"key": "yard", "name": "Двор", "description": "a sunny prison yard with a basketball hoop and fence"}],
    "sentences": [
        {"text": "Боб решил сбежать из тюрьмы с помощью обычной ложки.",
         "shots": [_shot("cell", ["bob"], "Bob holds a spoon up, sly grin", ["ложка", "spoon", "план"], "push_in",
                         accent="a tiny sparkle on the spoon"),
                   _shot("cell", ["bob"], "Bob stares at the brick wall, spoon in hand", ["стена", "wall", "взгляд"],
                         "pan_right")]},
        {"text": "Каждую ночь он скрёб стену, пока охранник громко храпел.",
         "shots": [_shot("cell", ["bob"], "Bob scraping the brick wall with the spoon at night",
                         ["скребёт", "scrape", "стена", "ночь"], "parallax_push", "скрежет", "cartoon motion lines"),
                   _shot("corridor", ["gena"], "Gena asleep on a chair, mouth open", ["спит", "sleep", "храп"],
                         "pull_out", "храп", "floating Zzz")]},
        {"text": "Через год в стене появилась крошечная дырка.",
         "shots": [_shot("cell", ["bob"], "Bob proudly points at a tiny hole in the wall", ["дырка", "hole", "гордость"],
                         "push_in", accent="a crack in the wall")]},
        {"text": "Боб заглянул в неё и увидел охранника с чашкой чая.",
         "shots": [_shot("cell", ["bob"], "Bob peeks into the tiny hole with one eye", ["заглянул", "peek", "дырка"],
                         "tilt_up"),
                   _shot("corridor", ["gena"], "Gena drinks tea from a big mug, smiling", ["чай", "tea", "кружка"],
                         "pan_left")]},
        {"text": "Охранник помахал ему кружкой и пожелал доброго утра.",
         "shots": [_shot("corridor", ["gena"], "Gena waves a big mug, friendly smile", ["машет", "wave", "кружка"],
                         "push_in")]},
        {"text": "Боб закрыл дырку плакатом с котиком и копал дальше.",
         "shots": [_shot("cell", ["bob"], "Bob hangs a cute cat poster over the hole", ["плакат", "poster", "котик"],
                         "pull_out"),
                   _shot("cell", ["bob"], "Bob digging behind the poster, sweat drops", ["копает", "dig", "пот"],
                         "pan_right", "скрежет", "sweat drops")]},
        {"text": "Однажды утром зазвенел будильник свободы.",
         "shots": [_shot("cell", ["bob"], "Bob jumps out of bed, an alarm clock ringing", ["будильник", "alarm", "утро"],
                         "push_in", "будильник", "cartoon motion lines")]},
        {"text": "Боб пролез в дыру и оказался прямо во дворе.",
         "shots": [_shot("yard", ["bob"], "Bob crawls out of a hole into the sunny yard", ["пролез", "crawl", "двор"],
                         "parallax_push"),
                   _shot("yard", ["bob"], "Bob looks around, happy and dusty", ["радость", "happy", "пыль"],
                         "pan_left")]},
        {"text": "Там его уже ждал охранник с пирогом.",
         "shots": [_shot("yard", ["gena"], "Gena holds a big cake, smiling", ["пирог", "cake", "ждал"], "pull_out")]},
        {"text": "Оказалось, дверь камеры всё это время была открыта.",
         "shots": [_shot("cell", ["bob"], "the cell door wide open, Bob shocked with jaw dropped",
                         ["дверь", "door", "шок"], "push_in", accent="little stars around the head"),
                   _shot("corridor", ["gena"], "Gena shrugs, holding a key ring", ["ключи", "keys", "пожал"],
                         "tilt_down")]},
        {"text": "Боб вздохнул, вернулся и закрыл её за собой.",
         "shots": [_shot("cell", ["bob"], "Bob closes the cell door behind himself, sighing", ["вздох", "sigh", "дверь"],
                         "pan_right")]},
        {"text": "Свобода — это хорошо, но ложку жалко.",
         "shots": [_shot("cell", ["bob"], "Bob sadly holds a bent spoon", ["ложка", "spoon", "грусть"], "push_in")]},
    ],
}


class FakeGemini:
    """Подделка Gemini: сценарий — STORY (или свой). Запоминает контекст (библиотека сцен, звуки)."""

    def __init__(self, story: dict | None = None):
        self.story = story or STORY
        self.calls: list[str] = []
        self.contexts: list[dict] = []
        self.rules: list[str] = []
        self.schemas: list[dict] = []

    def generate_content(self, model, payload):
        self.calls.append("script")
        self.rules.append(payload["contents"][0]["parts"][0]["text"])
        self.schemas.append(payload["generationConfig"]["responseSchema"])
        self.contexts.append(json.loads(payload["contents"][0]["parts"][1]["text"]))
        data = json.dumps(self.story, ensure_ascii=False)
        return {"candidates": [{"content": {"parts": [{"text": data}]}}]}


_REAL_CREATE_EPISODE = S.create_episode


def _create_dry_by_default(store, world_id, premise, duration=None, tone="", options=None, requested_by="agent", status="new"):
    """Эти тесты ведут эпизод без сети: в проекте без платного провайдера он — явный сухой прогон. Настоящий путь (голос,
    субтитры по речи, карточка) — фикстура ``paid`` и tests/test_releases.py."""
    opts = dict(options or {})
    if V._style_row(store, S.get_world(store, world_id)["project_id"])["provider"] == "none":
        opts.setdefault("dry_run", True)
    return _REAL_CREATE_EPISODE(store, world_id, premise, duration, tone, opts, requested_by, status)


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    monkeypatch.setattr(S, "create_episode", _create_dry_by_default)
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    monkeypatch.setattr(settings, "aivideo_enabled", False)
    monkeypatch.setattr(settings, "aivideo_month_cap_rub", 3000.0)
    monkeypatch.setattr(settings, "aivideo_max_budget_rub", 2500.0)
    monkeypatch.setattr(settings, "stories_episode_cap_rub", 200.0)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    # Паки звуков на машине есть не всегда — тесты видят только звуки, заведённые в _setup.
    monkeypatch.setattr(S, "SFX_PACKS", ())
    monkeypatch.setattr(PR, "MEASURED_IMAGE_PRICES", {})  # прайс ×2: 6 ₽ за картинку 1K
    # Whisper в тестах не грузим: слова — по длине текста (как сухой прогон).
    monkeypatch.setattr(X, "transcribe_words", lambda path: (_ for _ in ()).throw(RuntimeError("нет whisper")))
    client, store = _client(tmp_path, monkeypatch)
    return client, store, tmp_path


def _sound(store, tmp: Path, label: str, tags: str, freq: int = 200) -> int:
    """«Настоящая запись» владельца в «Файлах для монтажа» (тег «звук», как add_sound_from_url)."""
    from app.montage_assets import assets_dir, probe_asset

    path = assets_dir() / f"{label}.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1.2",
                    str(path)], check=True)
    meta = {**probe_asset(path), "width": 0, "height": 0}
    return int(store.create_montage_asset(kind="audio", label=label, description="", tags=tags, file_path=str(path),
                                          original_filename=path.name, mime_type="audio/mpeg",
                                          size_bytes=path.stat().st_size, **meta)["id"])


def _music(store) -> int:
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    music = settings.audio_dir / "story-music.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=80",
                    str(music)], check=True)
    return int(store.create_audio_track("фон", music, duration_sec=80)["id"])


def _setup(store, tmp: Path, provider: str = "none", music: bool = True, sounds: bool = True) -> dict:
    pid = V.create_ai_project(store, "Тюремные байки", provider=provider)["project"]["id"]
    world = S.save_world(store, pid, name="Игрушечная тюрьма", narrator="ироничный рассказчик, говорит на «ты»",
                         voice={"provider": "gemini", "voice": "Charon"},
                         music_track_id=_music(store) if music else None)
    ids = {"project_id": pid, "world_id": world["id"]}
    if sounds:
        ids["snore"] = _sound(store, tmp, "Храп", "звук,храп,snore", 120)
        ids["alarm"] = _sound(store, tmp, "Будильник", "звук,будильник,alarm", 900)
    return ids


def _layers(store, clip_id):
    return store.get_clip_plan(clip_id)["render_settings"]["layers"]


# --------------------------------------------------------------------------- #
# Сухой прогон: завязка → холст-клип с правильными слоями и временем
# --------------------------------------------------------------------------- #


def test_dry_run_end_to_end_builds_story_clip(env, monkeypatch):
    _client_, store, tmp = env
    monkeypatch.setattr(settings, "auto_mix", True)
    ids = _setup(store, tmp)
    fake = FakeGemini()
    ep = X.create(store, ids["world_id"], "заключённый сбегает из тюрьмы с помощью ложки", 50, "смешно", run=True,
                  client=fake)
    assert fake.calls == ["script"]
    assert ep["status"] == "done", (ep["error"], ep.get("notes"))
    # Сценаристу дали библиотеку сцен, актёров, локации и список настоящих звуков.
    ctx = fake.contexts[0]
    assert {"cast", "locations", "scene_library", "available_sfx", "camera_moves"} <= set(ctx)
    assert {s["tag"] for s in ctx["available_sfx"]} >= {"Храп", "Будильник"}
    # Новый актёрский состав и локации созданы в мире; листы, плиты, кадр стиля и сцены — сухим прогоном.
    world = S.get_world(store, ids["world_id"])
    chars = S.list_characters(store, ids["world_id"])
    locs = S.list_locations(store, ids["world_id"])
    assert {c["name"] for c in chars} == {"Боб", "Гена"} and all(c["sheet"] for c in chars)
    assert len(locs) == 3 and all(loc["plate"] for loc in locs)
    assert world["style_refs"], "кадр стиля мира нарисован"
    scenes = S.list_scenes(store, ids["world_id"])
    assert scenes and all(s["ready"] for s in scenes)
    assert G.ledger(store, ids["project_id"]) == []  # ни рубля

    plan = store.get_clip_plan(ep["clip_id"])
    render = plan["render_settings"]
    layers = render["layers"]
    images = sorted([l for l in layers if l["type"] == "image"], key=lambda l: l["start"])
    beats = ep["timeline"]["beats"]
    total = ep["timeline"]["duration"]
    assert len(images) == len(beats) >= 18
    # Сцены встык от 0 до конца, каждая 0.35–3.6 с; 25–30+ кадров в минуту.
    assert images[0]["start"] == 0 and abs(images[-1]["end"] - total) < 1e-6
    for a, b in zip(images, images[1:]):
        assert abs(a["end"] - b["start"]) < 1e-6
    durations = [l["end"] - l["start"] for l in images]
    assert max(durations[:-1]) <= X.REAL_SPLIT_SEC + 0.01 and min(durations) >= X.MIN_PLACED_SEC
    assert ep["timeline"]["shots_per_min"] >= 22
    assert all(l["props"]["w"] == 1080 for l in images)
    # Мягкая камера: Ken Burns ключами слоя (наезд/панорама) и 3D-наезд камеры (parallax_push).
    keyed = [l for l in images if l.get("keys")]
    assert len(keyed) >= len(images) // 2
    assert any("x" in l["keys"] for l in keyed) and any("scale" in l["keys"] for l in keyed)
    assert render["camera"], "parallax_push — ключи 3D-камеры"
    moves = [b["camera"] for b in beats]
    assert all(m in X.CAMERA_MOVES for m in moves)
    assert all(a != b for a, b in zip(moves, moves[1:])), "соседние кадры — разные движения камеры"
    # Звуки действий — только настоящие записи: храп и будильник из «Файлов для монтажа», скрежета нет.
    sfx = [l for l in layers if l["type"] == "audio" and l["props"].get("role") == "sfx"]
    refs = {str(l["props"]["asset_id"]) for l in sfx}
    assert refs == {str(ids["snore"]), str(ids["alarm"])}
    snore_beat = next(b for b in beats if (b.get("sfx") or {}).get("ref") == str(ids["snore"]))
    snore = next(l for l in sfx if str(l["props"]["asset_id"]) == str(ids["snore"]))
    assert snore_beat["start"] <= snore["start"] < snore_beat["end"]
    assert any("скрежет" in n and "синтетику не ставлю" in n for n in ep["notes"])
    # Голос, субтитры по ОДНОМУ слову в нижней трети (белые с тёмной обводкой), музыка с автосведением.
    narr = next(l for l in layers if l["type"] == "audio" and l["props"].get("role") == "voice")
    assert narr["start"] == 0 and narr["props"]["volume"] == 1.0
    caps = next(l for l in layers if l["type"] == "captions")
    assert caps["props"]["maxWords"] == 1 and caps["props"]["maxLines"] == 1
    assert caps["props"]["color"] == "#ffffff" and caps["props"]["stroke"]["width"] >= 8
    assert 0.66 <= caps["y"] <= 0.82
    subs = store.get_clip_plan_subtitles(ep["clip_id"])
    assert len(subs["words"]) >= 60
    assert render["music_on"] and render["track_id"] == world["music_track_id"]
    assert (render.get("mix") or {}).get("auto"), "автосведение после сборки истории"
    # Сцены помечены «использованы».
    assert all(s["uses"] >= 1 for s in S.list_scenes(store, ids["world_id"]) if s["id"] in {b["scene_id"]
                                                                                           for b in beats})


def test_story_clip_lands_in_the_world_project(env):
    from app import projects as P

    client, store, tmp = env
    ids = _setup(store, tmp)
    ep = X.create(store, ids["world_id"], "побег", 50, script=STORY, run=True)
    assert ep["status"] == "done", ep["error"]
    assert P.project_of_plan(store, ep["clip_id"]) == ids["project_id"]
    assert ep["editor_url"] == f"/clips/{ep['clip_id']}"


def test_title_card_and_cut_whooshes_are_optional(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = X.create(store, ids["world_id"], "побег", 50, script=STORY, options={"title_card": True}, run=True)
    assert ep["status"] == "done", ep["error"]
    layers = _layers(store, ep["clip_id"])
    title = next(l for l in layers if l["type"] == "text")
    assert title["props"]["text"] == "Побег с ложкой" and title["start"] == 0 and title["end"] <= X.TITLE_SEC
    # Название поверх сцен (дорожка выше), сцены — сам кадр, а не «пустой фон» для lint.
    scenes = [l for l in layers if l["type"] == "image"]
    assert scenes and title["track"] > max(l["track"] for l in scenes)
    assert all(l["props"].get("content") for l in scenes)
    from app.promo_lint import empty_frames
    assert empty_frames([l for l in layers if l["id"] != title["id"]], scenes[-1]["end"]) == []
    # Без записей звуков — ни одного синтетического звука, только заметка.
    assert not [l for l in layers if l["type"] == "audio" and l["props"].get("role") == "sfx"]
    assert any("храп" in n for n in ep["notes"])


# --------------------------------------------------------------------------- #
# Переиспользование сцен между эпизодами
# --------------------------------------------------------------------------- #


def test_second_episode_reuses_scenes_cast_and_locations(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp)
    first = X.create(store, ids["world_id"], "побег из тюрьмы", 50, run=True, client=FakeGemini())
    assert first["status"] == "done", first["error"]
    scenes_before = {s["id"] for s in S.list_scenes(store, ids["world_id"])}
    chars_before = {c["id"] for c in S.list_characters(store, ids["world_id"])}
    jobs_before = {j["id"] for j in G.list_jobs(store, ids["project_id"], limit=1000)}

    # Второй эпизод: «Gemini» снова описывает те же кадры и тех же новых актёров — сервис берёт готовое.
    fake = FakeGemini()
    second = X.create(store, ids["world_id"], "побег из тюрьмы, часть 2", 50, run=True, client=fake)
    assert second["status"] == "done", second["error"]
    assert {s["id"] for s in S.list_scenes(store, ids["world_id"])} == scenes_before
    assert {c["id"] for c in S.list_characters(store, ids["world_id"])} == chars_before
    assert len(S.list_locations(store, ids["world_id"])) == 3
    new_jobs = [j for j in G.list_jobs(store, ids["project_id"], limit=1000) if j["id"] not in jobs_before]
    assert [j for j in new_jobs if j["kind"] == "image"] == []
    assert any("из библиотеки" in n for n in second["notes"])
    assert {b["scene_id"] for b in second["timeline"]["beats"]} <= scenes_before
    # Сценарист второго эпизода видел библиотеку сцен и актёров первого.
    assert len(fake.contexts[0]["scene_library"]) == len(scenes_before)
    assert {c["key"] for c in fake.contexts[0]["cast"]} == {c["slug"] for c in S.list_characters(store,
                                                                                                  ids["world_id"])}

    # Третий: одна фраза с новым действием — ровно одна новая сцена (остальное готовое).
    story = copy.deepcopy(STORY)
    story["sentences"][-1]["shots"][0] = _shot("cell", ["bob"], "Bob juggles three oranges in the cell",
                                               ["жонглирует", "juggle", "апельсины"], "push_in")
    third = X.create(store, ids["world_id"], "часть 3", 50, run=True, client=FakeGemini(story))
    assert third["status"] == "done", third["error"]
    assert len(S.list_scenes(store, ids["world_id"])) == len(scenes_before) + 1


def test_scene_matching_needs_same_place_cast_and_similar_action():
    lib = [{"id": 1, "location_id": 5, "cast": [1], "action": "Bob scraping the brick wall with the spoon at night",
            "tags": ["скребёт", "scrape", "стена"], "accent": "", "ready": True},
           {"id": 2, "location_id": 5, "cast": [1, 2], "action": "Bob and Gena drink tea", "tags": ["чай"],
            "accent": "", "ready": True}]
    hit, sim = S.match_scene(lib, {"location_id": 5, "cast": [1], "action": "Bob scraping the wall with a spoon",
                                   "tags": ["scrape", "стена"]})
    assert hit and hit["id"] == 1 and sim >= S.REUSE_MIN
    hit, _ = S.match_scene(lib, {"location_id": 6, "cast": [1], "action": "Bob scraping the wall with a spoon"})
    assert hit is None  # другое место
    hit, _ = S.match_scene(lib, {"location_id": 5, "cast": [2, 1], "action": "Bob and Gena drink tea",
                                 "tags": ["чай"]})
    assert hit and hit["id"] == 2  # состав — без порядка
    hit, _ = S.match_scene(lib, {"location_id": 5, "cast": [1], "action": "Bob dances on the bunk bed",
                                 "tags": ["танец"]})
    assert hit is None  # другое действие


def test_beats_follow_shots_and_split_long_sentences():
    script = {"sentences": [
        {"text": "Боб решил сбежать из тюрьмы с помощью обычной ложки.", "shots": [{"action": "a"}, {"action": "b"}]},
        {"text": "Да.", "shots": [{"action": "c"}]},
        {"text": "Каждую ночь он долго скрёб стену, пока охранник громко храпел в коридоре у лампы.",
         "shots": [{"action": "d"}]}]}
    beats = X.split_beats(script)
    first = [b for b in beats if b["sentence"] == 0]
    assert [b["spec"]["action"] for b in first] == ["a", "b"]
    assert " ".join(b["text"] for b in first) == script["sentences"][0]["text"]
    long = [b for b in beats if b["sentence"] == 2]
    assert len(long) >= 2 and long[1]["variant"] == "alt" and long[1]["spec"]["action"] == "d"
    assert all(len(b["text"]) / X.CPS <= X.BEAT_MAX_SEC + 0.6 for b in beats)
    assert X.split_text("один два, три четыре", 2) == ["один два,", "три четыре"]


def test_sfx_resolution_uses_only_real_recordings():
    options = [{"ref": "sfx:freesound/punch.ogg", "label": "Удар кулаком", "tags": ["удар", "punch"],
                "source": "freesound"},
               {"ref": "17", "label": "Храп", "tags": ["звук", "храп"], "source": "library"}]
    assert S.resolve_sfx("храп", options)["ref"] == "17"
    assert S.resolve_sfx("snoring", options) is None or S.resolve_sfx("snore", options)["ref"] == "17"
    assert S.resolve_sfx("удар", options)["ref"] == "sfx:freesound/punch.ogg"
    assert S.resolve_sfx("sfx:freesound/punch.ogg", options)["ref"] == "sfx:freesound/punch.ogg"
    assert S.resolve_sfx("скрежет", options) is None
    assert S.resolve_sfx("", options) is None


# --------------------------------------------------------------------------- #
# Деньги: одна карточка на эпизод, ничего без «Разрешить», потолок эпизода
# --------------------------------------------------------------------------- #


class FakePaid:
    name = "polza"
    paid = True

    def __init__(self):
        self.posts: list[dict] = []

    def submit(self, params, refs):
        self.posts.append({"prompt": params.get("prompt") or "", "refs": len(refs)})
        return f"req-{len(self.posts)}"

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
    store = env[1]

    def hear_the_script(path):  # настоящий эпизод: речь есть и читает текст сценария
        row = store.db.query_one("SELECT id FROM story_episodes WHERE status = 'assembling' ORDER BY id DESC LIMIT 1")
        text = S.narration_text(S.get_episode(store, int(row["id"])))
        return [{"word": w, "start": round(i * 0.35, 3), "end": round(i * 0.35 + 0.3, 3)} for i, w in enumerate(text.split())]

    monkeypatch.setattr(X, "transcribe_words", hear_the_script)
    return env, fake


def test_one_owner_card_per_episode_and_episode_cap(paid, monkeypatch):
    (_client_, store, tmp), fake = paid
    ids = _setup(store, tmp, provider="polza")
    ep = X.create(store, ids["world_id"], "побег", 50, run=True, client=FakeGemini())
    assert ep["status"] == "awaiting_approval" and fake.posts == []
    # Бесплатная озвучка (Gemini TTS) уже есть; в карточке — картинки: кадр стиля, 2 листа, 3 плиты, сцены.
    assert S.narration_ref(store, S.get_episode(store, ep["id"])) is not None
    est = X.estimate(store, ep["id"])
    assert est["style_frames"] == 1 and est["sheets"] == 2 and est["plates"] == 3
    assert est["new_scenes"] >= 15 and est["narration_rub"] == 0
    assert est["images"] == 6 + est["new_scenes"]
    assert est["total_rub"] == pytest.approx(6.0 * est["images"]) and est["total_rub"] <= 200
    cards = [a for a in G.stage_approvals(store, ids["project_id"]) if a["stage"] == S.STAGE]
    assert len(cards) == 1
    card = A.get_approval(store, ep["approval_id"])
    assert card["status"] == "pending" and card["payload"]["amount_rub"] == pytest.approx(est["total_rub"])
    assert card["payload"]["group"] == f"story_ep:{ep['id']}"
    assert card["title"].startswith("История") and "новых сцен" in card["question"]
    assert "листов актёров 2" in card["question"] and "сцен из библиотеки" in card["question"]

    # Ничего не идёт без «Разрешить».
    G.run_pending(store)
    assert fake.posts == []
    assert X.advance(store, ep["id"])["status"] == "awaiting_approval"
    # Одобрено, но платная генерация выключена — держится, эпизод ждёт (не падает).
    A.decide(store, ep["approval_id"], "approve", actor="ui")
    G.run_pending(store)
    assert fake.posts == []
    assert X.advance(store, ep["id"])["status"] == "generating"

    monkeypatch.setattr(settings, "aivideo_enabled", True)
    G.run_approved(store, ep["approval_id"], confirm=True)
    assert len(fake.posts) == est["images"]
    # Основа — раньше сцен: кадр стиля первым, листы и плиты до первой сцены; у сцены — плита, лист и стиль.
    kinds = ["style" if "Style reference frame" in p["prompt"] else "sheet" if "model sheet" in p["prompt"]
             else "plate" if "background plate" in p["prompt"] else "scene" for p in fake.posts]
    assert kinds[0] == "style" and kinds.index("scene") > max(i for i, k in enumerate(kinds) if k != "scene")
    scene_posts = [p for p, k in zip(fake.posts, kinds) if k == "scene"]
    assert all(p["refs"] >= 3 for p in scene_posts)
    assert BOB.split(",")[0] in next(p["prompt"] for p in scene_posts if "Bob" in p["prompt"])
    done = X.advance(store, ep["id"])
    assert done["status"] == "done" and done["clip_id"], done["error"]
    led = G.ledger(store, ids["project_id"])
    assert len(led) == est["images"] and sum(l["amount_rub"] for l in led) == pytest.approx(est["total_rub"])

    # Второй эпизод тех же сцен — ни одной картинки, карточки нет.
    again = X.create(store, ids["world_id"], "побег 2", 50, run=True, client=FakeGemini())
    assert again["status"] == "done" and not again["approval_id"]
    assert len(fake.posts) == est["images"]

    # Потолок эпизода ниже суммы новых сцен — карточки нет, эпизод ждёт правки.
    monkeypatch.setattr(settings, "stories_episode_cap_rub", 5.0)
    story = copy.deepcopy(STORY)
    for n, sentence in enumerate(story["sentences"]):
        for shot in sentence["shots"]:
            shot["action"] = f"{shot['action']} in the rain number {n}"
            shot["tags"] = ["дождь", f"rain{n}"]
    v3 = X.create(store, ids["world_id"], "побег под дождём", 50, run=True, client=FakeGemini(story))
    assert v3["status"] == "failed" and "потолка одного ролика" in v3["error"] and "новых сцен" in v3["error"]
    assert len(fake.posts) == est["images"]


def test_reject_cancels_episode_and_single_regenerate_has_own_card(paid):
    (_client_, store, tmp), fake = paid
    ids = _setup(store, tmp, provider="polza")
    ep = X.create(store, ids["world_id"], "побег", 50, run=True, client=FakeGemini())
    A.decide(store, ep["approval_id"], "reject", comment="дорого", actor="ui")
    out = X.advance(store, ep["id"])
    assert out["status"] == "cancelled" and "rejected" in out["error"]
    assert fake.posts == []
    assert {j["status"] for j in G.list_jobs(store, approval_id=ep["approval_id"])} == {"cancelled"}
    scene = S.list_scenes(store, ids["world_id"])[0]
    req = S.request_item(store, f"story_scene:{scene['id']}")
    assert req["status"] == "awaiting_approval" and req["estimate"]["total_rub"] == pytest.approx(6.0)


# --------------------------------------------------------------------------- #
# Правки агентом, модуль, MCP, REST
# --------------------------------------------------------------------------- #


def test_agent_script_without_gemini_and_beat_override(env):
    _client_, store, tmp = env
    ids = _setup(store, tmp, music=False)
    ep = S.create_episode(store, ids["world_id"], "побег", 50)
    out = X.advance(store, ep["id"])  # Gemini нет — понятная ошибка с подсказкой
    assert out["status"] == "failed" and "story_set_script" in out["error"]
    X.set_script(store, ep["id"], STORY)
    X.plan_beats(store, ep["id"])
    beats = S.get_episode(store, ep["id"])["beats"]
    assert beats and all(b["scene_id"] for b in beats) and all(b["scene_new"] for b in beats)
    loc = S.list_locations(store, ids["world_id"])[0]
    bob = next(c for c in S.list_characters(store, ids["world_id"]) if c["name"] == "Боб")
    changed = X.update_beats(store, ep["id"], [
        {"i": 0, "new_scene": {"location_id": loc["id"], "cast": [bob["id"]], "action": "Bob eats soup with the spoon",
                               "tags": ["суп", "soup"]}, "camera": "tilt_down"},
        {"i": 1, "sfx": "храп"}])
    assert changed["status"] == "planned" and changed["beats"][0]["camera"] == "tilt_down"
    assert changed["beats"][1]["sfx"]["ref"] == str(ids["snore"])
    with pytest.raises(S.StoryError):
        X.update_beats(store, ep["id"], [{"i": 2, "sfx": "скрежет"}])  # записи нет — не синтетика
    with pytest.raises(S.StoryError):
        X.update_beats(store, ep["id"], [{"i": 999, "scene_id": 1}])
    final = X.advance(store, ep["id"])
    assert final["status"] == "done", final["error"]
    assert final["timeline"]["beats"][0]["camera"] == "tilt_down"
    again = X.reassemble(store, ep["id"])
    assert again["status"] == "done" and again["clip_id"] != final["clip_id"]


def test_module_tools_and_rest(env):
    client, store, tmp = env
    from app import agent_tools, chat_tools, modules
    from app.stories_tools import TOOL_NAMES

    names = {t.name for t in agent_tools._project_tools()}
    assert set(TOOL_NAMES) <= names
    assert all(modules.module_of_tool(n) == "stories" for n in TOOL_NAMES)
    assert set(TOOL_NAMES) <= set(chat_tools.group_tools("stories"))
    assert "ai_video" in modules.MODULES_BY_ID["stories"].depends
    assert "stories" in G.EXTENSIONS

    pid = V.create_ai_project(store, "Байки", provider="none")["project"]["id"]
    r = client.post(f"/api/projects/{pid}/stories/worlds", json={"name": "Тюрьма", "captions": {"y": 0.72}})
    assert r.status_code == 200 and r.json()["style_label"].startswith("3D-игрушки") and r.json()["captions"]["y"] == 0.72
    wid = r.json()["id"]
    r = client.post(f"/api/stories/worlds/{wid}/characters", json={"name": "Боб", "identity_card": BOB})
    assert r.status_code == 200 and r.json()["character"]["sheet"] is None
    r = client.post(f"/api/stories/worlds/{wid}/locations", json={"name": "Камера", "description": "a tiny cell",
                                                                    "generate": True})
    # Провайдер none у проекта — не сухой прогон: настоящая картинка платная, значит карточка, а не заглушка.
    assert r.status_code == 200 and r.json()["generation"]["status"] == "awaiting_approval"
    r = client.get(f"/api/projects/{pid}/stories")
    assert r.status_code == 200 and not r.json()["worlds"][0]["locations"][0]["plate"]
    assert r.json()["episode_cap_rub"] == 200
    r = client.post(f"/api/stories/worlds/{wid}/episodes", json={"premise": "побег", "duration": 120})
    assert r.status_code == 400
    r = client.post(f"/api/stories/worlds/{wid}/episodes", json={"premise": "побег из тюрьмы", "duration": 50})
    assert r.status_code == 200 and r.json()["status"] == "new"
    eid = r.json()["id"]
    r = client.put(f"/api/stories/episodes/{eid}/script", json=STORY)
    assert r.status_code == 200 and r.json()["status"] == "scripted"
    r = client.post(f"/api/stories/episodes/{eid}/continue", json={})
    assert r.status_code == 200 and r.json()["status"] == "done", r.json()["error"]
    assert client.get("/api/stories/sfx?q=храп").status_code == 200

    from app.agent_tools import ToolContext, run_tool

    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "story_status", {"episode_id": eid})
    assert ok and out["status"] == "done" and out["clip_id"] and out["beats"] and out["timeline"]["shots_per_min"]
    out, ok = run_tool(ctx, "story_create", {"world_id": wid, "premise": "побег", "duration": 10})
    assert not ok and "30–75" in out["error"]
    out, ok = run_tool(ctx, "story_overview", {"project_id": pid})
    assert ok and out["worlds"][0]["scenes"]
    modules.set_modules(store, {"stories": False})
    try:
        assert "story_create" not in {t.name for t in agent_tools.list_tools(True, store)}
        assert client.get(f"/api/projects/{pid}/stories").status_code == modules.HTTP_STATUS
    finally:
        modules.set_modules(store, {"stories": True})


# --------------------------------------------------------------------------- #
# Настоящий рендер компоновщиком (короткий сухой прогон, заглушки сцен)
# --------------------------------------------------------------------------- #


def test_dry_run_render_with_real_compositor(env):
    _client_, store, tmp = env
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    ids = _setup(store, tmp, music=False)
    short = {**STORY, "sentences": STORY["sentences"][:4]}
    ep = S.create_episode(store, ids["world_id"], "побег коротко", 30)
    X.set_script(store, ep["id"], short)
    done = X.advance(store, ep["id"])
    assert done["status"] == "done", done["error"]
    from app.dubbing import probe_duration
    from app.render import ClipRenderService

    preset = store.create_ffmpeg_preset("story test", output_width=144, output_height=256, fps=12)
    clip = ClipRenderService(store).render_clip_plan(done["clip_id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    path = Path(clip["output_path"])
    assert path.is_file() and path.stat().st_size > 5_000
    assert abs(probe_duration(path) - done["timeline"]["duration"]) < 1.0
