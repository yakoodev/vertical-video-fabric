"""Истории: сценарий — схема, контекст, нормализация (из ``app/stories_gen.py``)."""

from __future__ import annotations

import re
from typing import Any

from app import projects as P
from app import stories as S
from app import stories_disaster as D

CPS = 14.0  # символов русской речи рассказчика в секунду


MAX_NEW_CAST = 4
MAX_NEW_LOCATIONS = 4
MAX_SHOTS_PER_SENTENCE = 3
CAMERA_MOVES = ("push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "parallax_push", "static")


# --------------------------------------------------------------------------- #
# 1. Сценарий
# --------------------------------------------------------------------------- #

SCRIPT_RULES = """\
Ты сценарист вертикальных роликов-историй (Shorts/TikTok/Reels) в формате «рассказчик за кадром + мультяшные
3D-сцены»: на КАЖДУЮ фразу — новая неподвижная сцена в одном стиле. Рассказчик (narrator) смешно рассказывает
короткую историю по завязке (premise): ПО-РУССКИ, живым разговорным языком, короткими фразами (до 12 слов),
без эмодзи и хэштегов. Структура:
- первая фраза — крючок: интрига за 2 секунды («Боб решил сбежать из тюрьмы с помощью ложки.»);
- дальше — развитие: каждая фраза — действие, которое можно показать одной картинкой;
- поворот (неожиданное препятствие или открытие) примерно на двух третях;
- последняя фраза — панчлайн, смешная развязка.
Смешно — это не пересказ завязки. Пересказ («Боб пролез через лаз прямо к свободе», «Оказывается, тоннель вёл в
кабинет») — брак. Каждые 2–3 фразы — шутка:
- конкретная абсурдная деталь вместо общих слов: не «прятал землю», а «выносил землю в карманах и делал вид, что
  просто поправился»; не «ложка стиралась», а «сточил сорок ложек и одну зубную щётку»;
- рассказчик говорит буднично и невозмутимо даже про абсурд; короткие рубленые фразы («Двадцать лет. Чайной
  ложкой.»), ремарки-уточнения («Свобода пахнет… ванилью?»);
- реплика героя в кавычках — где она смешнее описания;
- панчлайн — колбэк к детали из начала (ложки, карманы) или неожиданный вывод, а не мораль и не «самый … в
  истории». Финал не объясняет шутку.
Объём всего текста — примерно duration_sec × 14 символов (±10%). Числа пиши словами.
Каждой фразе (sentences[].text) — 1–2 кадра (shots), примерно один кадр на 2 секунды речи: фраза до 30
символов — 1 кадр, длиннее — 2. Кадр — неподвижная сцена в стиле мира:
- location — key локации из locations (ПЕРЕИСПОЛЬЗУЙ, новые только если без них никак) или из new_locations;
- characters — keys актёров в кадре (0–3) из cast или new_characters; переиспользуй cast;
- action — ПО-АНГЛИЙСКИ 8–25 слов: кто что делает, поза, выражение лица, ключевые предметы; без надписей;
- tags — 4–8 коротких тегов по-русски и по-английски (действие, эмоция, предметы);
- camera — одно из camera_moves (push_in наезд, pull_out отъезд, pan_left/pan_right панорама, tilt_up/tilt_down,
  parallax_push 3D-наезд, static); соседние кадры — разные движения;
- sfx — звук действия: tag из available_sfx или короткое слово («храп», «скрежет», «будильник»), только если в
  кадре звучащее действие; иначе пусто. Не чаще чем в каждом втором кадре;
- accent — ПО-АНГЛИЙСКИ маленький мультяшный акцент («cartoon motion lines», «floating Zzz», «a crack in the
  wall», «sweat drops», «little stars around the head») или пусто;
- scene_id — id сцены из scene_library, если она подходит точно (то же место, те же актёры, то же действие):
  готовая сцена бесплатна, новая стоит денег.
new_characters[] (не больше 4): key, name (по-русски), identity_card — ПО-АНГЛИЙСКИ 15–40 слов: простой
персонаж с большой круглой головой, цвета (hex), одежда, одна отличительная деталь; persona — характер по-русски.
new_locations[] (не больше 4): key, name, description — ПО-АНГЛИЙСКИ 15–40 слов: уютное 3D-место, мебель,
предметы, свет.
title — название до 60 символов; logline — одна фраза, о чём история. Соблюдай правила владельца
(owner_rules) и тон (tone). Не повторяй прошлые истории (previous).
Верни ТОЛЬКО JSON по схеме.
"""


def _shot_schema() -> dict:
    return {"type": "object", "properties": {
        "location": {"type": "string"}, "characters": {"type": "array", "items": {"type": "string"}},
        "action": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "camera": {"type": "string"}, "sfx": {"type": "string"}, "accent": {"type": "string"},
        "scene_id": {"type": "integer"}}, "required": ["action"]}


def script_schema(fmt: str = S.FORMAT_STORY) -> dict:
    schema = _base_schema()
    return D.extend_schema(schema) if fmt == S.FORMAT_DISASTER else schema


def script_rules(fmt: str = S.FORMAT_STORY) -> str:
    return D.SCRIPT_RULES if fmt == S.FORMAT_DISASTER else SCRIPT_RULES


def _base_schema() -> dict:
    return {"type": "object", "properties": {
        "title": {"type": "string"}, "logline": {"type": "string"},
        "new_characters": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "name": {"type": "string"}, "identity_card": {"type": "string"},
            "persona": {"type": "string"}}, "required": ["key", "name", "identity_card"]}},
        "new_locations": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["key", "name", "description"]}},
        "sentences": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "shots": {"type": "array", "items": _shot_schema()}},
            "required": ["text", "shots"]}}},
        "required": ["title", "sentences"]}


def _sfx_brief(options: list[dict]) -> list[dict]:
    # Свои звуки владельца — первыми: паков много, и лимит не должен их срезать.
    ordered = sorted(options, key=lambda o: o.get("source") != "library")
    return [{"tag": o["label"], "ref": o["ref"], "tags": o["tags"][:5]} for o in ordered[:80]]


def script_context(store: Any, ep: dict) -> dict:
    world = S.get_world(store, int(ep["world_id"]))
    project_id = int(ep["project_id"])
    rules = [{"kind": r["kind"], "text": r["text"]} for r in P.list_rules(store, project_id=project_id)]
    project = P.get_project(store, project_id, include_sources=False)
    previous = [f"{e['title'] or ''} — {e['premise']}"[:200] for e in S.list_episodes(store, world_id=int(world["id"]),
                                                                                       limit=12)
                if int(e["id"]) != int(ep["id"])]
    scenes = S.list_scenes(store, int(world["id"]))
    chars = S.list_characters(store, int(world["id"]))
    locs = S.list_locations(store, int(world["id"]))
    slug_of_char = {int(c["id"]): c["slug"] for c in chars}
    slug_of_loc = {int(loc["id"]): loc["slug"] for loc in locs}
    fmt = S.episode_format(world, ep)
    extra: dict[str, Any] = {"format": fmt}
    if fmt == S.FORMAT_DISASTER:
        extra["series"] = D.series_context(store, ep)
    return {**extra, "channel": project["name"], "channel_about": project.get("description") or "",
            "style": world["style_label"], "narrator": world["narrator"],
            "cast": [{"key": c["slug"], "name": c["name"], "what": c["identity_card"], "persona": c["persona"]}
                     for c in chars],
            "locations": [{"key": loc["slug"], "name": loc["name"], "what": loc["description"]} for loc in locs],
            "scene_library": [{"id": s["id"], "location": slug_of_loc.get(int(s["location_id"] or 0), ""),
                               "characters": [slug_of_char.get(c, "") for c in s["cast"]], "action": s["action"],
                               "tags": s["tags"][:8]} for s in scenes[-120:]],
            "premise": ep["premise"], "duration_sec": ep["duration_target"], "tone": ep["tone"],
            "target_chars": int(ep["duration_target"] * CPS), "owner_rules": rules, "previous": previous,
            "camera_moves": list(CAMERA_MOVES), "available_sfx": _sfx_brief(S.sfx_options(store, project_id))}


def _sentence(text: Any) -> str:
    text = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def _key(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def _materialize(store: Any, world: dict, data: dict, warnings: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Ключи актёров и локаций сценария → id мира. Новые (``new_characters``/``new_locations``) создаются
    в мире без картинок — листы и плиты нарисуются в карточке эпизода, если их сцены новые."""
    chars = S.list_characters(store, int(world["id"]))
    locs = S.list_locations(store, int(world["id"]))
    char_map: dict[str, int] = {}
    loc_map: dict[str, int] = {}
    for c in chars:
        for k in (c["slug"], c["name"], str(c["id"])):
            char_map[_key(k)] = int(c["id"])
    for loc in locs:
        for k in (loc["slug"], loc["name"], str(loc["id"])):
            loc_map[_key(k)] = int(loc["id"])
    for spec in (data.get("new_characters") or [])[:MAX_NEW_CAST]:
        if not isinstance(spec, dict) or not P._text(spec.get("name")) or not P._text(spec.get("identity_card")):
            continue
        key = _key(spec.get("key") or spec["name"])
        if key in char_map or _key(spec["name"]) in char_map:
            char_map.setdefault(key, char_map.get(_key(spec["name"]), 0) or char_map[key])
            continue
        c = S.save_character(store, int(world["id"]), name=spec["name"], identity_card=spec["identity_card"],
                             persona=spec.get("persona") or "", origin="generated")
        for k in (key, c["slug"], c["name"]):
            char_map[_key(k)] = int(c["id"])
    for spec in (data.get("new_locations") or [])[:MAX_NEW_LOCATIONS]:
        if not isinstance(spec, dict) or not P._text(spec.get("name")) or not P._text(spec.get("description")):
            continue
        key = _key(spec.get("key") or spec["name"])
        if key in loc_map or _key(spec["name"]) in loc_map:
            loc_map.setdefault(key, loc_map.get(_key(spec["name"]), 0) or loc_map[key])
            continue
        loc = S.save_location(store, int(world["id"]), name=spec["name"], description=spec["description"],
                              origin="generated")
        for k in (key, loc["slug"], loc["name"]):
            loc_map[_key(k)] = int(loc["id"])
    extra = len(data.get("new_characters") or []) - MAX_NEW_CAST
    if extra > 0:
        warnings.append(f"новых актёров больше {MAX_NEW_CAST} — лишние {extra} не созданы")
    return char_map, loc_map


def _shot(raw: Any, char_map: dict, loc_map: dict, scene_ids: set[int], warnings: list[str], n: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    scene_id = int(raw.get("scene_id") or 0)
    if scene_id and scene_id not in scene_ids:
        warnings.append(f"фраза {n}: сцены {scene_id} нет в библиотеке мира — опишу заново")
        scene_id = 0
    action = P._long_text(raw.get("action"), 400)
    if not action and not scene_id:
        return None
    loc_key = _key(raw.get("location") if raw.get("location") is not None else raw.get("location_id"))
    location_id = loc_map.get(loc_key) if loc_key else None
    if loc_key and location_id is None:
        warnings.append(f"фраза {n}: локации «{raw.get('location')}» нет — кадр без своей локации")
    cast: list[int] = []
    for who in raw.get("characters") or raw.get("cast") or []:
        cid = char_map.get(_key(who))
        if cid is None:
            warnings.append(f"фраза {n}: актёра «{who}» нет в мире — без него")
        elif cid not in cast:
            cast.append(cid)
    camera = str(raw.get("camera") or "").strip()
    return {"location_id": location_id, "cast": sorted(cast)[:4], "action": action,
            "tags": S._tags(raw.get("tags") or [])[:10], "camera": camera if camera in CAMERA_MOVES else "",
            "sfx": P._text(raw.get("sfx"), 120), "accent": P._text(raw.get("accent"), 120),
            **({"scene_id": scene_id} if scene_id else {})}


def normalize_script(store: Any, world: dict, data: dict, ep: dict | None = None) -> dict:
    """Сценарий (Gemini или агент) → фразы с кадрами (id актёров и локаций мира) и полный текст.
    Сериал-катастрофа (``ep`` с форматом disaster): у фраз hud/hud_kind/role, поля серии и проверки формулы."""
    disaster = S.episode_format(world, ep) == S.FORMAT_DISASTER
    warnings: list[str] = []
    raw_sentences = data.get("sentences")
    if not raw_sentences and P._text(data.get("text")):
        raw_sentences = [{"text": s, "shots": []} for s in re.split(r"(?<=[.!?…])\s+", str(data["text"]))
                         if s.strip()]
    if not isinstance(raw_sentences, list) or not raw_sentences:
        raise S.StoryError("пустой сценарий: нужны sentences [{text, shots [{location, characters, action, tags, "
                           "camera, sfx, accent}]}] (или text — тогда сцены из библиотеки по словам)")
    char_map, loc_map = _materialize(store, world, data, warnings)
    scene_ids = {int(s["id"]) for s in S.list_scenes(store, int(world["id"]))}
    sentences = []
    for n, raw in enumerate(raw_sentences, 1):
        if isinstance(raw, str):
            raw = {"text": raw}
        if not isinstance(raw, dict) or not P._text(raw.get("text")):
            continue
        shots = [s for s in (_shot(x, char_map, loc_map, scene_ids, warnings, n)
                             for x in (raw.get("shots") or [])[:MAX_SHOTS_PER_SENTENCE]) if s]
        sentences.append({"text": _sentence(raw["text"]), "shots": shots,
                          **(D.sentence_extra(raw) if disaster else {})})
    if not sentences:
        raise S.StoryError("в сценарии нет фраз")
    text = " ".join(s["text"] for s in sentences)
    if len(sentences[0]["text"]) > 70:
        warnings.append(f"крючок {len(sentences[0]['text'])} символов — длинно, первая фраза должна звучать ≤ 3 с")
    shots = sum(len(s["shots"]) for s in sentences)
    est = round(len(text) / CPS, 1)
    if shots and est and shots / est * 60 < 18:
        warnings.append(f"кадров {shots} на {est:.0f} с — меньше 18 в минуту, добавь кадры фразам")
    script = {"title": P._text(data.get("title"), 100), "logline": P._text(data.get("logline"), 300),
              "sentences": sentences, "text": text, "chars": len(text), "est_sec": est, "shots": shots,
              "warnings": warnings}
    if disaster:
        script = D.finish_script(script, data, ep or {})
        script["chars"] = len(script["text"])
    return script


def script_words(sentences: list[dict]) -> int:
    return sum(len(str(s.get("text") or "").split()) for s in sentences)


def _word_list(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9]+", str(text or "").lower().replace("ё", "е"))


def normalize_beats(store: Any, world: dict, script: dict, raw: Any) -> list[dict]:
    """Биты, написанные автором черновика: ``[{text, location, characters, action, tags, camera, sfx, accent, scene_id}]``.
    Форма та же, что у разбивки по сценарию: подбор сцен потом идёт по ним, а не режет заново. Озвучка привязывается к
    битам по словам, поэтому их текст подряд обязан быть текстом сценария."""
    if not isinstance(raw, list) or not raw:
        raise S.StoryError("биты: нужен непустой список [{text, action, camera, sfx}]")
    char_map, loc_map = _materialize(store, world, {}, script["warnings"])
    scene_ids = {int(s["id"]) for s in S.list_scenes(store, int(world["id"]))}
    ends: list[int] = []
    for sentence in script["sentences"]:
        ends.append((ends[-1] if ends else 0) + len(_word_list(sentence["text"])))
    beats: list[dict] = []
    said = 0
    for n, item in enumerate(raw, 1):
        item = {"text": item} if isinstance(item, str) else item
        text = " ".join(str(item.get("text") or "").split()) if isinstance(item, dict) else ""
        if not text:
            raise S.StoryError(f"бит {n}: нужен text, кусок текста сценария")
        sentence = next((k for k, end in enumerate(ends) if said < end), len(ends) - 1)
        beats.append({"i": n - 1, "text": text, "sentence": sentence, "shot": 0,
                      "spec": _shot(item, char_map, loc_map, scene_ids, script["warnings"], n), "variant": None,
                      "est_sec": round(len(text) / CPS, 2)})
        said += len(_word_list(text))
    wanted, given = _word_list(script["text"]), _word_list(" ".join(b["text"] for b in beats))
    if wanted != given:
        at = next((k for k, (a, b) in enumerate(zip(wanted, given)) if a != b), min(len(wanted), len(given)))
        raise S.StoryError(f"текст битов подряд не совпадает с текстом сценария: слово {at + 1}, в сценарии "
                           f"«{wanted[at] if at < len(wanted) else 'конец'}», в битах «{given[at] if at < len(given) else 'конец'}». "
                           "В text — слова рассказчика, что на экране — в action")
    return beats


# Сценарий не двигает такие эпизоды: черновик ждёт «Запустить», отменённый продолжают только явно.
_SCRIPT_KEEPS_STATUS = (S.DRAFT_STATUS, "cancelled")


def set_script(store: Any, episode_id: int, data: dict, source: str = "agent") -> dict:
    """Сценарий эпизода → фразы с кадрами; биты и сборка сбрасываются (дальше — подбор сцен).
    Черновик и отменённый эпизод остаются в своём статусе: сценарий сохранён, работник их не берёт."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "assembling":
        raise S.StoryError("эпизод собирается — дождись сборки")
    world = S.get_world(store, int(ep["world_id"]))
    script = normalize_script(store, world, data or {}, ep)
    script["source"] = source
    target = float(ep["duration_target"])
    if abs(script["est_sec"] - target) > target * 0.25:
        script["warnings"].append(f"текст ≈ {script['est_sec']:.0f} с при цели {target:.0f} с")
    given = (data or {}).get("beats")
    beats = normalize_beats(store, world, script, given) if ep["status"] == S.DRAFT_STATUS and given else []
    # Клип и слова прошлой сборки остаются: новая сборка станет его версией и перенесёт правки субтитров.
    fields = {"script": script, "beats": beats, "timeline": {}, "title": script["title"] or P._text(ep["premise"], 100),
              "approval_id": None}
    moving = tuple(s for s in S.STATUSES if s not in _SCRIPT_KEEPS_STATUS)
    # Сравнить и записать: отмена могла прийти, пока Gemini писал текст, и сценарий её не перетрёт.
    if not S.set_status(store, episode_id, "scripted", moving, **fields, error=""):
        current = S.get_episode(store, episode_id, full=False)["status"]
        if current in _SCRIPT_KEEPS_STATUS:
            S.set_status(store, episode_id, current, (current,), **fields)
    return S.get_episode(store, episode_id)
