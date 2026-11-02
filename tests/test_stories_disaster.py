"""Истории: жанр-шаблон «сериал-катастрофа» (app/stories_disaster.py). Сухой прогон с подделкой Gemini:
сценарий по формуле, серийность (части, «ранее»), HUD-плашки, название и титр части, без музыки, обложка по
шаблону и описание «вопрос + своя версия + теги». Ни сети, ни платных вызовов."""

from __future__ import annotations

import copy

import pytest

from app import aivideo as V
from app import stories as S
from app import stories_disaster as D
from app import stories_gen as X
from app.settings import settings
from test_stories import FakeGemini, _music, env  # noqa: F401 - env — фикстура

DANYA = "a low-poly young man Danya, 24, red hair, short beard, bright yellow hooded raincoat, olive backpack"


def _shot(loc, action, tags, camera="", sfx=""):
    return {"location": loc, "characters": ["danya"], "action": action, "tags": tags, "camera": camera, "sfx": sfx}


def _s(text, role, loc, action, tags, camera, hud="", kind=""):
    out = {"text": text, "role": role, "shots": [_shot(loc, action, tags, camera)]}
    if hud:
        out["hud"] = hud
    if kind:
        out["hud_kind"] = kind
    return out


# «Если блэкаут застал вас на 36 этаже» ~70 с: хук на «вы» с одной ошибкой, проблема → решение каждые ~10 с,
# цифры HUD, ошибка ближе к концу, «Была лишь одна проблема…» и жуткая деталь.
DISASTER = {
    "title": "Если блэкаут застал вас на 36 этаже",
    "logline": "Даня пережидает блэкаут в прихожей, но открывает дверь не тому",
    "part_title": "Она осталась наверху",
    "hook_words": "не открывай дверь",
    "viewer_question": "А ты бы открыл дверь?",
    "hashtags": ["#блэкаут", "#сериал", "#36этаж", "выживание"],
    "mistake": "Даня открыл дверь, чтобы проверить соседей",
    "cliffhanger": "соседей на этаже никогда не было, а в прихожей стоят чужие ботинки",
    "new_characters": [{"key": "danya", "name": "Даня", "identity_card": DANYA, "persona": "осторожный и упрямый"}],
    "new_locations": [
        {"key": "hall", "name": "Прихожая", "description": "a dark low-poly apartment hallway, shoe rack, front door"},
        {"key": "flat", "name": "Квартира", "description": "a dark low-poly apartment on the 36th floor, big windows"},
        {"key": "stairs", "name": "Лестница", "description": "a dark low-poly stairwell with railings and exit sign"}],
    "sentences": [
        _s("Если блэкаут застал вас на 36 этаже, не бегите вниз.", "hook", "flat",
           "over-the-shoulder view behind Danya at a dark window, city lights going out", ["окно", "блэкаут"],
           "push_in", "36 ЭТАЖ"),
        _s("Безопаснее всего там, где вы снимаете обувь.", "hook", "hall",
           "Danya stands by the shoe rack in the dark hallway", ["прихожая", "обувь"], "pan_right"),
        _s("Но только если не совершить одну ошибку.", "hook", "hall",
           "close view of the front door handle in the dark", ["дверь", "ошибка"], "parallax_push"),
        _s("В 21:40 свет погас во всём доме, и Даня остался в темноте.", "problem", "flat",
           "Danya stands in a pitch dark living room, only his yellow raincoat visible", ["темнота", "dark"],
           "pull_out", "21:40"),
        _s("Он нашёл фонарик в прихожей, но заряд был всего 9%.", "solution", "hall",
           "Danya holds a weak flashlight, beam flickering", ["фонарик", "flashlight"], "tilt_up", "ЗАРЯД 9%",
           "resource"),
        _s("Через 10 минут батареи остыли, в квартире стало +12 градусов.", "problem", "flat",
           "Danya touches a cold radiator, breath steam visible", ["холод", "cold"], "pan_left", "+12°C"),
        _s("Даня закрыл все двери и сел в прихожей, где нет окон.", "solution", "hall",
           "Danya sits on the floor of the windowless hallway wrapped in a blanket", ["одеяло", "blanket"], "push_in"),
        _s("В 22:15 на лестнице кто-то начал стучать по перилам.", "problem", "stairs",
           "top-down view of a dark stairwell, a shadow by the railings", ["стук", "knock"], "tilt_down", "22:15"),
        _s("Он посмотрел в глазок, но на площадке было пусто.", "solution", "hall",
           "Danya peeks through the door peephole, fisheye view of an empty landing", ["глазок", "peephole"],
           "pull_out"),
        _s("Стук спустился на 2 этажа ниже и замолчал.", "problem", "stairs",
           "the stairwell two floors below, flashlight beam on empty steps", ["лестница", "stairs"], "pan_right",
           "▼ 34 ЭТАЖ"),
        _s("Даня выключил фонарик, чтобы сберечь последние 3%.", "solution", "hall",
           "Danya switches off the flashlight, only a thin moonlight line on the floor", ["луна", "moonlight"],
           "parallax_push", "ЗАРЯД 3%"),
        _s("А потом он открыл дверь, чтобы проверить соседей.", "mistake", "hall",
           "Danya opens the front door a crack, cold air and darkness beyond", ["дверь", "door"], "push_in"),
        _s("Была лишь одна проблема.", "finale", "hall",
           "over-the-shoulder view behind Danya looking at the dark landing", ["тишина", "silence"], "static"),
        _s("Соседей на 36 этаже никогда не было, а в прихожей стояли чужие ботинки.", "finale", "hall",
           "a pair of strange wet boots standing on Danya's shoe rack", ["ботинки", "boots"], "tilt_down"),
    ],
}


def _world(store, music: bool = True) -> dict:
    pid = V.create_ai_project(store, "Катастрофы", provider="none")["project"]["id"]
    return S.save_world(store, pid, name="Блэкаут", format="disaster", voice={"provider": "gemini", "voice": "Charon"},
                        music_track_id=_music(store) if music else None)


def _layers(store, clip_id):
    return store.get_clip_plan(clip_id)["render_settings"]["layers"]


# --------------------------------------------------------------------------- #
# 1. Сценарий по формуле, 2. HUD и титры, 3. без музыки
# --------------------------------------------------------------------------- #


def test_disaster_world_episode_script_hud_titles_and_no_music(env):
    _client_, store, _tmp = env
    world = _world(store)
    assert world["format"] == "disaster" and world["music"] == "none"
    assert world["style_passport"] == D.STYLE and "лоу-поли" in world["style_label"]
    fake = FakeGemini(DISASTER)
    ep = X.create(store, world["id"], "блэкаут застал парня на 36 этаже", run=True, client=fake)
    assert ep["status"] == "done", (ep["error"], ep.get("notes"))
    # Свои правила и схема сценариста формата; серия — часть 1, «ранее» пусто.
    assert "Если [катастрофа] застала вас [в месте]" in fake.rules[0] and "одну ошибку" in fake.rules[0]
    assert "Была лишь одна проблема" in fake.rules[0] and "ЧАСТЬ {series.part + 1}" in fake.rules[0]
    schema = fake.schemas[0]
    assert {"hud", "hud_kind", "role"} <= set(schema["properties"]["sentences"]["items"]["properties"])
    assert {"part_title", "hook_words", "viewer_question", "hashtags"} <= set(schema["properties"])
    ctx = fake.contexts[0]
    assert ctx["format"] == "disaster" and ctx["series"] == {"part": 1, "previously": []}
    assert ep["options"]["format"] == "disaster" and ep["options"]["part"] == 1
    assert ep["duration_target"] == 75 and ep["tone"] == "напряжённо"
    script = ep["script"]
    assert script["part_title"] == "ОНА ОСТАЛАСЬ НАВЕРХУ" and script["hook_words"] == "НЕ ОТКРЫВАЙ ДВЕРЬ"
    formula = [w for w in script["warnings"] if any(k in w for k in ("хук", "ошибк", "цифры", "одна проблема",
                                                                      "призыв", "поворот"))]
    assert formula == []
    kinds = {s["hud"]: s["hud_kind"] for s in script["sentences"] if s.get("hud")}
    assert kinds == {"36 ЭТАЖ": "info", "21:40": "info", "ЗАРЯД 9%": "resource", "+12°C": "danger",
                     "22:15": "info", "▼ 34 ЭТАЖ": "danger", "ЗАРЯД 3%": "resource"}

    render = store.get_clip_plan(ep["clip_id"])["render_settings"]
    layers = render["layers"]
    tl = ep["timeline"]
    total, speech_end = tl["duration"], tl["speech_end"]
    images = sorted([l for l in layers if l["type"] == "image"], key=lambda l: l["start"])
    assert images[0]["start"] == 0  # без чёрного экрана: первый кадр — сцена
    # Название серии поверх первого кадра 1–2 с и «ЧАСТЬ 1».
    title = next(l for l in layers if l["id"] == "st_title")
    assert title["start"] == 0 and 1.0 <= title["end"] <= 2.0 and title["props"]["text"] == DISASTER["title"]
    assert title["track"] > images[0]["track"]
    assert next(l for l in layers if l["id"] == "st_title_part")["props"]["text"] == "ЧАСТЬ 1"
    # Конец — сразу после последнего слова + титр следующей части на 2 с; субтитры кончаются со словом.
    part = next(l for l in layers if l["id"] == "st_part")
    assert part["props"]["text"] == "ЧАСТЬ 2 — ОНА ОСТАЛАСЬ НАВЕРХУ" and tl["part_card"] == part["props"]["text"]
    assert part["start"] == pytest.approx(speech_end) and part["end"] == pytest.approx(total)
    assert total - speech_end == pytest.approx(D.PART_SEC, abs=0.02)
    caps = next(l for l in layers if l["type"] == "captions")
    assert caps["end"] == pytest.approx(speech_end)
    # HUD: плашка на каждую фразу с hud, цвет по признаку, «игровой» шрифт, тёмная подложка, въезд,
    # в безопасной зоне сверху, не на субтитрах, внутри своей фразы и до титра части.
    hud = [l for l in layers if l["id"].startswith("st_hud")]
    assert len(hud) == len(kinds) == len(tl["hud"])
    by_text = {l["props"]["text"]: l for l in hud}
    assert by_text["ЗАРЯД 9%"]["props"]["color"] == D.HUD_COLORS["resource"] == "#ffd400"
    assert by_text["+12°C"]["props"]["color"] == by_text["▼ 34 ЭТАЖ"]["props"]["color"] == "#ff3b30"
    assert by_text["21:40"]["props"]["color"] == "#ffffff"
    assert by_text["+12°C"]["loop"]["type"] == "pulse"
    for l in hud:
        assert l["props"]["font"] == "Roboto Condensed" and l["props"]["bg"]["opacity"] < 1
        assert l["in"]["type"] in ("slideRight", "slideLeft") and l["in"]["duration"] > 0
        assert 0.10 < l["y"] < 0.80 and abs(l["y"] - caps["y"]) >= 0.12
        assert 0.0 < l["x"] < 1.0 and l["end"] <= speech_end + 1e-6 and l["end"] - l["start"] >= D.HUD_MIN_SEC - 1e-6
        assert l["track"] < title["track"]
    beats = tl["beats"]
    n_charge = next(i for i, s in enumerate(script["sentences"]) if s.get("hud") == "ЗАРЯД 9%")
    span = [b for b in beats if b["sentence"] == n_charge]
    assert span[0]["start"] <= by_text["ЗАРЯД 9%"]["start"] < span[-1]["end"]
    # Музыка не вшита, хотя у мира есть трек; заметка — ставить в TikTok.
    assert not render.get("music_on")
    assert D.MUSIC_NOTE in ep["notes"]
    # Описание к публикации — уже в таймлайне (без Gemini).
    meta = tl["publish"]
    assert meta["description"].startswith("А ты бы открыл дверь?") and "свою версию" in meta["description"]
    assert 3 <= len(meta["hashtags"]) <= 5 and "#блэкаут" in meta["hashtags"]


def test_music_auto_keeps_track_for_disaster(env):
    _client_, store, _tmp = env
    world = _world(store)
    ep = X.create(store, world["id"], "блэкаут", 65, script=DISASTER, options={"music": "auto"}, run=True)
    assert ep["status"] == "done", ep["error"]
    render = store.get_clip_plan(ep["clip_id"])["render_settings"]
    assert render["music_on"] and render["track_id"] == world["music_track_id"]


# --------------------------------------------------------------------------- #
# Серийность: части, «ранее», крючок прошлой части
# --------------------------------------------------------------------------- #


def test_series_parts_previously_and_hook_continuation(env):
    _client_, store, _tmp = env
    world = _world(store, music=False)
    first = X.create(store, world["id"], "блэкаут, часть 1", run=True, client=FakeGemini(DISASTER))
    assert first["status"] == "done", first["error"]
    second_story = copy.deepcopy(DISASTER)
    second_story["part_title"] = "Часть 3 — лифт поехал сам, хотя света в доме нет"  # длиннее HUD, с номером
    fake = FakeGemini(second_story)
    second = X.create(store, world["id"], "что было за дверью", run=True, client=fake)
    assert second["status"] == "done", second["error"]
    assert second["options"]["part"] == 2
    series = fake.contexts[0]["series"]
    assert series["part"] == 2 and [p["part"] for p in series["previously"]] == [1]
    prev = series["previously"][0]
    assert prev["title"] == DISASTER["title"] and prev["mistake"] == DISASTER["mistake"]
    assert prev["next_part_title"] == "ОНА ОСТАЛАСЬ НАВЕРХУ"
    assert "ЧАСТЬ 2 — ОНА ОСТАЛАСЬ НАВЕРХУ" in series["continue_from"] and "чужие ботинки" in series["continue_from"]
    layers = _layers(store, second["clip_id"])
    assert (next(l for l in layers if l["id"] == "st_part")["props"]["text"]
            == "ЧАСТЬ 3 — ЛИФТ ПОЕХАЛ САМ, ХОТЯ СВЕТА В ДОМЕ НЕТ")
    assert next(l for l in layers if l["id"] == "st_title_part")["props"]["text"] == "ЧАСТЬ 2"
    # Номер части можно задать; отменённая часть не занимает номер; обычная история — не часть сериала.
    fifth = S.create_episode(store, world["id"], "часть пять", options={"part": 5})
    assert fifth["options"]["part"] == 5
    X.cancel(store, fifth["id"])
    assert D.next_part(store, world["id"]) == 3
    story = S.create_episode(store, world["id"], "смешная история", 50, options={"format": "story"})
    assert story["options"] == {"format": "story"} and D.next_part(store, world["id"]) == 3


# --------------------------------------------------------------------------- #
# Проверки формулы, призыв не в конце, правила HUD
# --------------------------------------------------------------------------- #


def test_formula_checks_and_cta_moved_to_middle(env):
    _client_, store, _tmp = env
    world = _world(store, music=False)
    ep = S.create_episode(store, world["id"], "плохая серия")
    bad = {"title": "Блэкаут", "part_title": "", "sentences": [
        {"text": "Даня сидел дома и смотрел сериал."}, {"text": "Вдруг погас свет."},
        {"text": "Он пошёл искать фонарик."}, {"text": "Фонарик нашёлся в шкафу."},
        {"text": "Свет так и не дали."}, {"text": "Даня лёг спать."},
        {"text": "Подпишись, чтобы не пропустить часть 2."}]}
    script = X.normalize_script(store, world, bad, ep)
    texts = [s["text"] for s in script["sentences"]]
    assert texts.index("Подпишись, чтобы не пропустить часть 2.") == 3  # из конца — в середину
    assert script["sentences"][3]["role"] == "cta" and texts[-1] == "Даня лёг спать."
    joined = " | ".join(script["warnings"])
    for key in ("хук не на «вы»", "нет обещания «одной ошибки»", "цифры в", "Была лишь одна проблема",
                "та самая ошибка", "нет part_title", "призыв в конце"):
        assert key in joined, key
    # Длина по жанру: катастрофа 60–90 с, история 30–75.
    with pytest.raises(S.StoryError, match="60–90"):
        S.create_episode(store, world["id"], "коротко", 50)
    with pytest.raises(S.StoryError, match="30–75"):
        S.create_episode(store, world["id"], "длинно", 80, options={"format": "story"})


def test_hud_kind_text_and_slot_rules():
    assert D.hud_text("-41°c") == "−41°C" and D.hud_kind(D.hud_text("-41°c")) == "danger"
    assert D.hud_kind("02:07") == "info" and D.hud_kind("22 / 23 ЭТАЖ") == "info"
    assert D.hud_kind("9%") == "resource" and D.hud_kind("ЗАРЯД 9%") == "resource"
    assert D.hud_kind("▼ 00:45") == "danger" and D.hud_kind("ВОДА 2 Л") == "resource"
    assert D.hud_kind("02:07", "danger") == "danger"  # признак сценария важнее слов
    assert len(D.hud_text("очень длинная надпись на экране больше лимита")) <= D.HUD_MAX_CHARS
    x, y = D.hud_slot("02:07", [], 0.74)
    assert x < 0.5 and y < 0.2  # по умолчанию — сверху слева
    x, y = D.hud_slot("02:07", [[0.0, 0.05, 0.4, 0.3]], 0.74)
    assert x > 0.5 and y < 0.2  # лицо слева сверху — плашка справа
    _x, y = D.hud_slot("02:07", [[0.0, 0.05, 1.0, 0.3]], 0.6)
    assert abs(y - 0.6) >= 0.12  # не на субтитрах
    meta = D.publish_meta({"premise": "x", "options": {"part": 3},
                           "script": {"title": "Если потоп застал вас в метро", "viewer_question": "Пиши 1 или 2",
                                      "hashtags": []}})
    assert meta["description"] == f"{D.DEFAULT_QUESTION} {D.ASK}" and "1 или 2" not in meta["description"]
    assert meta["title"].endswith("часть 3") and 3 <= len(meta["hashtags"]) <= 5


# --------------------------------------------------------------------------- #
# 3–4. Обложка по шаблону и описание при публикации (рендер — подделка)
# --------------------------------------------------------------------------- #


def test_finish_publish_template_covers_and_description(env, monkeypatch):
    _client_, store, _tmp = env
    from app import render as R
    from app.cover_variants import list_covers, read_small

    world = _world(store, music=False)
    ep = X.create(store, world["id"], "блэкаут", 65, script=DISASTER, run=True)
    assert ep["status"] == "done", ep["error"]
    plan = store.get_clip_plan(ep["clip_id"])

    def fake_render(self, clip_plan_id, **_kw):
        settings.clip_dir.mkdir(parents=True, exist_ok=True)
        out = settings.clip_dir / f"story-{clip_plan_id}.mp4"
        out.write_bytes(b"\x00" * 64)
        clip = store.create_clip(plan["source_id"], clip_plan_id=clip_plan_id, title="x")
        return store.finish_clip_render(clip["id"], "succeeded", output_path=out, duration_sec=60, width=1080,
                                        height=1920)

    monkeypatch.setattr(R.ClipRenderService, "render_clip_plan", fake_render)
    gemini = FakeGemini()
    monkeypatch.setattr(X, "gemini_client", lambda: gemini)
    out = X.finish_publish(store, ep["id"], ep["clip_id"])
    assert gemini.calls == []  # обложка и описание — без ИИ
    assert out["covers"] == 3, S.get_episode(store, ep["id"])["notes"]
    clip_id = out["render"]["clip"]
    covers = list_covers(store, clip_id)
    assert len(covers) == 3 and {c["style"] for c in covers} == {"disaster"} and covers[0]["chosen"]
    assert covers[0]["text"] == "ЕСЛИ БЛЭКАУТ ЗАСТАЛ ВАС НА 36 ЭТАЖЕ · СЕРИЯ 1 · НЕ ОТКРЫВАЙ ДВЕРЬ"
    clip = store.get_clip(clip_id)
    assert clip["cover_url"] and store.get_clip_cover_path(clip_id)
    # Шаблон: красная плашка с крючком под жёлтым «СЕРИЯ N».
    layout = D.cover_layout(D.cover_texts(S.get_episode(store, ep["id"])))
    from app.cover_variants import get_cover

    img = read_small(get_cover(store, covers[0]["id"])["path"], D.COVER_SIZE)
    assert img is not None and img.shape[:2] == (1920, 1080)
    px, py, _pw, ph = layout["pill"]
    b, g, r = (int(v) for v in img[py + ph // 2, px + ph // 4])
    assert r > 170 and g < 90 and b < 90, (r, g, b)
    # Описание: один вопрос зрителю + «напиши свою версию» + 3–5 тегов (не «1 или 2»).
    meta = clip["publish_meta"]
    assert meta["source"] == "story" and meta["description"].count("?") == 1
    assert "свою версию" in meta["description"] and "1 или 2" not in meta["description"]
    assert 3 <= len(meta["hashtags"]) <= 5
    assert clip["title"] == "Если блэкаут застал вас на 36 этаже — часть 1"


# --------------------------------------------------------------------------- #
# MCP и REST
# --------------------------------------------------------------------------- #


def test_mcp_and_rest_disaster_format(env):
    client, store, _tmp = env
    from app.agent_tools import ToolContext, run_tool

    pid = V.create_ai_project(store, "Катастрофы", provider="none")["project"]["id"]
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "story_save_world", {"project_id": pid, "name": "Метро", "format": "disaster"})
    assert ok and out["world"]["format"] == "disaster" and out["world"]["music"] == "none"
    wid = out["world"]["id"]
    out, ok = run_tool(ctx, "story_create", {"world_id": wid, "premise": "потоп в метро", "duration": 50})
    assert not ok and "60–90" in out["error"]
    out, ok = run_tool(ctx, "story_create", {"world_id": wid, "premise": "потоп в метро", **{
        k: DISASTER[k] for k in ("title", "sentences", "new_characters", "new_locations", "part_title", "hook_words",
                                 "viewer_question", "hashtags", "mistake", "cliffhanger")}, "duration": 70, "wait": True})
    assert ok and out["status"] == "done", out
    ep = out["episode"]
    assert ep["options"]["format"] == "disaster" and ep["options"]["part"] == 1
    assert ep["script"]["part_title"] == "ОНА ОСТАЛАСЬ НАВЕРХУ" and ep["timeline"]["part_card"].startswith("ЧАСТЬ 2")
    assert ep["timeline"]["hud"]
    # Обычный мир, эпизод-катастрофа через REST: формат на эпизоде, длина по умолчанию 75.
    r = client.post(f"/api/projects/{pid}/stories/worlds", json={"name": "Байки"})
    assert r.status_code == 200 and r.json()["format"] == "story"
    r = client.post(f"/api/stories/worlds/{r.json()['id']}/episodes", json={"premise": "лавина в горах",
                                                                           "format": "disaster"})
    assert r.status_code == 200, r.text
    assert r.json()["options"]["format"] == "disaster" and r.json()["duration_target"] == 75
    r = client.patch(f"/api/stories/worlds/{wid}", json={"music": "auto"})
    assert r.status_code == 200 and r.json()["music"] == "auto"
    assert client.patch(f"/api/stories/worlds/{wid}", json={"format": "comedy"}).status_code == 400


def test_part_card_fits_safe_width():
    # Титр «ЧАСТЬ N — …» любой длины — в безопасной зоне: перенос по maxWidth, поля подложки внутри, без
    # «удара» с перелётом за края (раньше «ЧАСТЬ 2 — КТО СТУЧАЛ» обрезалось слева и справа).
    from app import stories_disaster as D

    ep = {"script": {"part_title": "Кто стучал в двери лифта посреди ночи на двадцать третьем этаже"}, "options": {}}
    lay = D.part_layer(ep, 50.0, 52.0, "#151515", "st_")
    p = lay["props"]
    assert p["maxWidth"] + 2 * p["bg"]["padX"] + 2 * p["stroke"]["width"] <= D.SAFE_WIDTH <= 1080 * 0.88
    assert lay["in"]["type"] != "stomp"
    # Самое длинное слово титра (≈0.6 em на букву у узкого жирного шрифта) влезает в строку.
    longest = max(len(w) for w in D.part_card_text(ep).split())
    assert longest * p["size"] * 0.6 <= p["maxWidth"]
