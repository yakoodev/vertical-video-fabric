"""Истории: конвейер «завязка → готовый ролик» (docs/guides/stories.md).

Шаги эпизода (``story_episodes.status``), каждый повторяем и идемпотентен; ведёт ``advance`` (работник
``StoriesWorker`` или агент ``story_continue``):

1. ``new`` → **сценарий** (Gemini, бесплатно): крючок первой фразой, развитие, поворот, панчлайн;
   русский, 45–60 с; на КАЖДУЮ фразу 1–2 кадра: кто, где, действие (по-английски), камера, звук,
   акцент. Новые актёры и локации — только если без них никак (сначала переиспользуются свои).
   Gemini недоступен — ``failed`` с подсказкой: агент пишет сценарий сам (``story_set_script``).
2. ``scripted`` → **биты и сцены**: фраза → биты по 1.2–3 с (по кадрам фразы; длинная фраза с одним
   кадром — тот же кадр с другим движением камеры). На каждый бит — готовая сцена библиотеки (то же
   место, те же актёры, похожее действие) или новая (рисуется по карточке). Звук бита — настоящая
   запись (паки, «Файлы для монтажа»); записи нет — заметка, синтетики нет.
3. ``planned`` → **оценка и деньги**: бесплатная озвучка (Gemini TTS / сухой прогон) — сразу; новые
   сцены, листы, плиты, кадр стиля и платная озвучка — ОДНА карточка владельцу (этап ``stories``).
4. ``awaiting_approval`` / ``generating`` → ждём «Разрешить» и запуски (работник ИИ-видео).
5. ``assembling`` → **сборка**: слова озвучки (Whisper; сухой прогон — по длине текста) → биты по
   времени → холст-клип: сцены слоями ``image`` с мягкой камерой (Ken Burns ключами слоя или наша
   3D-камера ``parallax_push``), звуки действий, озвучка, субтитры по ОДНОМУ слову в нижней трети,
   музыка мира под голосом (``auto_mix``). По желанию — плашка-название, вжухи на склейках, рендер,
   обложки, заголовок/описание.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import aivideo_gen as G
from app import autopilot as A
from app import explainer_gen as X
from app import projects as P
from app import stories as S
from app import stories_disaster as D
from app.settings import settings

_log = logging.getLogger(__name__)

CPS = 14.0  # символов русской речи рассказчика в секунду
BEAT_MIN_SEC = 1.2
BEAT_MAX_SEC = 3.0
BEAT_MERGE_SEC = 0.7  # кусок фразы короче — сливается с соседним
REAL_SPLIT_SEC = 3.6  # после озвучки бит длиннее — делится той же сценой с другой камерой
MIN_PLACED_SEC = 0.35
TAIL_SEC = 0.8
MAX_NEW_SCENES = 40
MAX_NEW_CAST = 4
MAX_NEW_LOCATIONS = 4
MAX_SHOTS_PER_SENTENCE = 3
CAMERA_MOVES = ("push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "parallax_push", "static")
CAMERA_CYCLE = ("push_in", "pan_right", "pull_out", "pan_left", "parallax_push", "tilt_up")
CAMERA_ALT = {"push_in": "pull_out", "pull_out": "push_in", "pan_left": "pan_right", "pan_right": "pan_left",
              "tilt_up": "tilt_down", "tilt_down": "tilt_up", "parallax_push": "pan_left", "static": "push_in"}
SFX_VOLUME = 0.55
SFX_OFFSET_SEC = 0.12  # звук действия — чуть после склейки
SFX_MAX_SEC = 3.5
TITLE_SEC = 2.2
LAYER_PREFIX = "st_"

# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def gemini_client() -> Any:
    """Клиент Gemini или None (нет ключа). Тесты подменяют."""
    return X.gemini_client()


def _ask(client: Any, rules: str, context: dict, schema: dict, temperature: float = 0.9) -> dict:
    try:
        return X._ask(client, rules, context, schema, temperature)
    except S.StoryError:
        raise
    except V.AivError as exc:
        raise S.StoryError(str(exc)) from exc


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


def write_script(store: Any, episode_id: int, client: Any = None) -> dict:
    ep = S.get_episode(store, episode_id)
    client = client or gemini_client()
    if client is None:
        raise S.StoryError("Gemini не настроен (GEMINI_API_KEY) — напиши сценарий сам: story_set_script(episode_id, "
                           "sentences [{text, shots [{location, characters, action, camera, sfx, accent}]}])")
    fmt = S.episode_format(S.get_world(store, int(ep["world_id"])), ep)
    rules = script_rules(fmt)
    blender = S.is_3d(ep) and S.episode_renderer(ep) == S.RENDERER_BLENDER
    if blender:
        from app import series3d_blender as SB

        rules += ("\nМЕСТА И ВЕЩИ — ТОЛЬКО ИЗ СПИСКА (сцена покажет ровно их; другого места или вещи в кадре не будет — "
                  "не упоминай улицу, квартиру изнутри, подвал, окна и т.п.). Надпись на стене (год, номер, слово) — можно.\n"
                  + SB.writer_places())
        lo, hi = SB.SCRIPT_WORDS
        rules += (f"\nДЛИНА — ЖЁСТКИЙ ЛИМИТ: серия {SB.SCRIPT_SEC[0]}–{SB.SCRIPT_SEC[1]} с, это {lo}–{hi} слов во всех "
                  f"фразах вместе (не больше {hi}). Длиннее — не влезет в серию.")
    data = _ask(client, rules, script_context(store, ep), script_schema(fmt), 0.9)
    if blender:
        data = fit_script_to_places(client, data)
        data = fit_script_length(client, data)
    return set_script(store, episode_id, data, source="gemini")


def script_words(sentences: list[dict]) -> int:
    return sum(len(str(s.get("text") or "").split()) for s in sentences)


def fit_script_length(client: Any, data: dict) -> dict:
    """3D-серия (Blender): текст длиннее лимита слов (``SCRIPT_WORDS``, 55–65 с) — сократить фразы (одна попытка:
    та же роль, цифры и смысл, короче); не сократилось — предупреждение."""
    from app import series3d_blender as SB

    sentences = data.get("sentences") or []
    lo, hi = SB.SCRIPT_WORDS
    words = script_words(sentences)
    if words <= hi or not sentences:
        return data
    rules = (f"Сократи фразы серии до {lo}–{hi} слов ВСЕГО (сейчас {words}). Номер i и роль фразы те же, цифры и смысл "
             "те же, хук и финал не трогай по смыслу; можно убрать лишние детали. Верни все фразы.")
    ctx = {"sentences": [{"i": i, "role": s.get("role") or "", "text": s.get("text")} for i, s in enumerate(sentences)]}
    schema = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}, "required": ["i", "text"]}}},
        "required": ["sentences"]}
    try:
        fixed = _ask(client, rules, ctx, schema, 0.4)
    except S.StoryError:
        fixed = {}
    for item in fixed.get("sentences") or []:
        i = int(item.get("i", -1))
        if 0 <= i < len(sentences) and str(item.get("text") or "").strip():
            sentences[i] = {**sentences[i], "text": str(item["text"]).strip()}
    data["sentences"] = sentences
    left = script_words(sentences)
    if left > hi:
        data["warnings"] = list(data.get("warnings") or []) + [f"текст {left} слов — длиннее {hi} (≈ {SB.SCRIPT_SEC[1]} с)"]
    return data


def fit_script_to_places(client: Any, data: dict) -> dict:
    """Фразы, где сценарист назвал место/вещь вне набора локаций, — переписать (тот же смысл, место из списка)."""
    from app import series3d_blender as SB

    sentences = data.get("sentences") or []
    bad = SB.offlist_mentions(sentences)
    if not bad:
        return data
    rules = ("Перепиши указанные фразы сериала-катастрофы так, чтобы место и вещи были ТОЛЬКО из списка (смысл, цифры, "
             "длина и роль фразы — те же):\n" + SB.writer_places())
    ctx = {"sentences": [{"i": i, "text": sentences[i].get("text"), "problem": what} for i, what in bad]}
    schema = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}, "required": ["i", "text"]}}},
        "required": ["sentences"]}
    try:
        fixed = _ask(client, rules, ctx, schema, 0.5)
    except S.StoryError:
        return data
    for item in fixed.get("sentences") or []:
        i = int(item.get("i", -1))
        if 0 <= i < len(sentences) and str(item.get("text") or "").strip():
            sentences[i] = {**sentences[i], "text": str(item["text"]).strip()}
    data["sentences"] = sentences
    left = SB.offlist_mentions(sentences)
    data.setdefault("warnings", [])
    if left:
        data["warnings"] = list(data.get("warnings") or []) + [f"фраза {i + 1}: «{w}» — места нет в наборе" for i, w in left]
    return data


def set_script(store: Any, episode_id: int, data: dict, source: str = "agent") -> dict:
    """Сценарий эпизода → фразы с кадрами; биты и сборка сбрасываются (дальше — подбор сцен)."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "assembling":
        raise S.StoryError("эпизод собирается — дождись сборки")
    world = S.get_world(store, int(ep["world_id"]))
    script = normalize_script(store, world, data or {}, ep)
    script["source"] = source
    target = float(ep["duration_target"])
    if abs(script["est_sec"] - target) > target * 0.25:
        script["warnings"].append(f"текст ≈ {script['est_sec']:.0f} с при цели {target:.0f} с")
    S.set_status(store, episode_id, "scripted", None, script=script, beats=[], words=[], timeline={},
                 title=script["title"] or P._text(ep["premise"], 100), error="", approval_id=None, clip_id=None)
    return S.get_episode(store, episode_id)


# --------------------------------------------------------------------------- #
# 2. Биты и сцены
# --------------------------------------------------------------------------- #


def _est(text: str) -> float:
    return len(text) / CPS


def split_text(text: str, n: int) -> list[str]:
    """Фраза → ``n`` кусков примерно поровну по длине, разрез — на границе слова (лучше после запятой,
    двоеточия, тире)."""
    words = text.split()
    n = max(1, min(n, len(words)))
    if n == 1:
        return [text]
    ends, pos = [], 0
    for w in words:
        pos += len(w) + 1
        ends.append(pos)
    total = ends[-1]
    cuts: list[int] = []
    prev = 0
    for j in range(1, n):
        quota = total * j / n
        lo, hi = prev + 1, len(words) - (n - j)
        best, best_cost = lo, float("inf")
        for k in range(lo, hi + 1):
            cost = abs(ends[k - 1] - quota) - (0.25 * total / n if re.search(r"[,:;—–]$", words[k - 1]) else 0.0)
            if cost < best_cost:
                best, best_cost = k, cost
        cuts.append(best)
        prev = best
    out, start = [], 0
    for c in cuts + [len(words)]:
        out.append(" ".join(words[start:c]))
        start = c
    return [p for p in out if p]


def split_beats(script: dict) -> list[dict]:
    """Фразы → биты: по кадру фразы на кусок (1.2–3 с по длине текста); длинная фраза с одним кадром —
    тот же кадр ещё раз (``variant: alt`` — другое движение камеры)."""
    beats: list[dict] = []
    for n, sentence in enumerate(script.get("sentences") or []):
        text, shots = sentence["text"], list(sentence.get("shots") or [])
        est = _est(text)
        need = max(1, math.ceil(est / BEAT_MAX_SEC))
        fit = max(1, int(est // BEAT_MIN_SEC))
        count = max(need, min(len(shots) or 1, fit))
        pieces = split_text(text, count)
        for j, piece in enumerate(pieces):
            if shots:
                k = min(j, len(shots) - 1)
                beat = {"text": piece, "sentence": n, "shot": k, "spec": dict(shots[k]),
                        "variant": "alt" if j >= len(shots) else None}
            else:
                beat = {"text": piece, "sentence": n, "shot": None, "spec": None, "variant": None}
            beats.append(beat)
    merged: list[dict] = []
    for beat in beats:
        prev = merged[-1] if merged else None
        if (prev and _est(beat["text"]) < BEAT_MERGE_SEC and prev["sentence"] == beat["sentence"]
                and _est(prev["text"] + " " + beat["text"]) <= BEAT_MAX_SEC + 0.6):
            prev["text"] = f"{prev['text']} {beat['text']}"
            continue
        merged.append(dict(beat))
    for i, beat in enumerate(merged):
        beat["i"] = i
        beat["est_sec"] = round(_est(beat["text"]), 2)
    return merged


def _pick_camera(want: str, prev: str | None, n: int) -> str:
    move = want if want in CAMERA_MOVES else CAMERA_CYCLE[n % len(CAMERA_CYCLE)]
    if prev and move == prev:
        move = CAMERA_ALT.get(move, "push_in")
    return move


def _scene_for_text(scenes: list[dict], text: str, avoid: set[int]) -> dict | None:
    """Без кадра в сценарии: готовая сцена, где больше общих основ с текстом бита (соседние — разные)."""
    from app import explainer as E

    words = E.stems(text)
    ranked = sorted(((len(words & (S.scene_terms(s) | E.stems(s.get("title")))), -int(s.get("uses") or 0), s)
                     for s in scenes if s["ready"]), key=lambda x: (-x[0], x[1], int(x[2]["id"])))
    for _score, _uses, scene in ranked:
        if int(scene["id"]) not in avoid:
            return scene
    return ranked[0][2] if ranked else None


def plan_beats(store: Any, episode_id: int) -> dict:
    """Биты эпизода со сценами (готовые из библиотеки или новые), камерой и звуками."""
    ep = S.get_episode(store, episode_id)
    if not S.narration_text(ep):
        raise S.StoryError("у эпизода нет сценария")
    world = S.get_world(store, int(ep["world_id"]))
    beats = split_beats(ep["script"])
    if S.is_3d(ep):
        # 3D-движок: картинок сцен нет — биты только для времени фраз, HUD и звуков (кадры ставит режиссура
        # app/series3d.py по словам озвучки при сборке).
        return _plan_beats_3d(store, episode_id, beats)
    library = S.list_scenes(store, int(world["id"]))
    by_id = {int(s["id"]): s for s in library}
    created: list[dict] = []
    notes: list[str] = []
    options = S.sfx_options(store, int(ep["project_id"]))
    missing_sfx: list[str] = []
    prev_camera: str | None = None
    for n, beat in enumerate(beats):
        spec = beat.get("spec")
        prev = beats[n - 1] if n else None
        if prev and beat.get("variant") == "alt" and prev.get("sentence") == beat["sentence"]:
            beat["scene_id"] = prev["scene_id"]
        elif spec and spec.get("scene_id") and int(spec["scene_id"]) in by_id:
            beat["scene_id"] = int(spec["scene_id"])
        elif spec:
            hit, sim = S.match_scene(library, spec)
            if hit is not None:
                beat["scene_id"] = int(hit["id"])
                if int(hit["id"]) in {int(c["id"]) for c in created}:
                    beat["reuse"] = "episode"
                else:
                    beat["reuse"] = "library"
                    notes.append(f"бит {beat['i']}: сцена #{hit['id']} «{P._text(hit['title'], 40)}» из библиотеки "
                                 f"(сходство {sim})")
            elif len(created) >= MAX_NEW_SCENES:
                alt = _scene_for_text([s for s in library if s["ready"]] or library, beat["text"], set())
                if alt is None:
                    raise S.StoryError(f"больше {MAX_NEW_SCENES} новых сцен на эпизод — сократи кадры")
                beat["scene_id"] = int(alt["id"])
                notes.append(f"бит {beat['i']}: больше {MAX_NEW_SCENES} новых сцен не рисую — взял #{alt['id']}")
            else:
                scene = S.save_scene(store, int(world["id"]), location_id=spec.get("location_id") or 0,
                                     cast=spec.get("cast") or [], action=spec["action"], accent=spec.get("accent") or "",
                                     tags=spec.get("tags") or [], title=P._text(beat["text"], 60), origin="generated")
                library.append(scene)
                created.append(scene)
                by_id[int(scene["id"])] = scene
                beat["scene_id"] = int(scene["id"])
        else:
            avoid = {int(prev["scene_id"])} if prev and prev.get("scene_id") else set()
            alt = _scene_for_text(library, beat["text"], avoid)
            if alt is None:
                raise S.StoryError("у фраз нет кадров, а библиотека сцен пуста — опиши кадры (story_set_script с "
                                   "shots) или добавь сцены (story_add_scene)")
            beat["scene_id"] = int(alt["id"])
            notes.append(f"бит {beat['i']}: кадра в сценарии нет — сцена по словам #{alt['id']}")
        if prev and prev.get("scene_id") == beat["scene_id"] and beat.get("variant") != "alt":
            beat["variant"] = "alt"  # та же сцена подряд — другое движение камеры
        beat["camera"] = _pick_camera((spec or {}).get("camera") or "", prev_camera, n)
        prev_camera = beat["camera"]
        tag = (spec or {}).get("sfx") if beat.get("variant") != "alt" else ""
        beat["sfx"] = None
        if tag:
            hit = S.resolve_sfx(tag, options)
            if hit is not None:
                beat["sfx"] = {"tag": tag, "ref": hit["ref"], "label": hit["label"]}
            elif tag not in missing_sfx:
                missing_sfx.append(tag)
    if missing_sfx:
        notes.append("нет записей для звуков: " + ", ".join(f"«{t}»" for t in missing_sfx[:8])
                     + " — синтетику не ставлю; попроси у владельца ссылку и добавь add_sound_from_url (тег «звук»)")
    _decorate(store, int(world["id"]), beats)
    new = sorted({b["scene_id"] for b in beats if b.get("scene_new")})
    reused = sorted({b["scene_id"] for b in beats} - set(new))
    S.set_status(store, episode_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "episode_id": int(episode_id), "beats": len(beats), "scenes": len({b["scene_id"] for b in beats}),
            "new_scenes": new, "reused_scenes": reused, "notes": notes}


def _plan_beats_3d(store: Any, episode_id: int, beats: list[dict]) -> dict:
    """Биты 3D-серии: без сцен библиотеки (``scene_id`` нет), звук кадра — настоящая запись по тегу сценария."""
    options = S.sfx_options(store, int(S.get_episode(store, episode_id)["project_id"]))
    notes: list[str] = ["3D-движок: сцены не рисуются — кадры, свет и звуки событий ставит режиссура по озвучке"]
    for beat in beats:
        tag = (beat.get("spec") or {}).get("sfx") if beat.get("variant") != "alt" else ""
        hit = S.resolve_sfx(tag, options) if tag else None
        beat.update({"scene_id": None, "scene_title": "", "scene_new": False, "camera": "",
                     "sfx": {"tag": tag, "ref": hit["ref"], "label": hit["label"]} if hit else None})
    S.set_status(store, episode_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "episode_id": int(episode_id), "beats": len(beats), "scenes": 0, "new_scenes": [],
            "reused_scenes": [], "notes": notes, "engine": S.ENGINE_3D}


def _decorate(store: Any, world_id: int, beats: list[dict]) -> None:
    scenes = {int(s["id"]): s for s in S.list_scenes(store, world_id)}
    for beat in beats:
        scene = scenes.get(int(beat.get("scene_id") or 0))
        beat["scene_title"] = scene["title"] if scene else ""
        beat["scene_new"] = bool(scene and not scene["ready"])


def update_beats(store: Any, episode_id: int, changes: list[dict]) -> dict:
    """Агент/владелец правит биты до сборки: ``[{i, scene_id | new_scene {location_id, cast, action, tags,
    accent}, camera?, sfx?}]`` → эпизод снова «планируется» (новая оценка и карточка)."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "assembling":
        raise S.StoryError("эпизод собирается — дождись сборки")
    beats = ep.get("beats") or []
    if not beats:
        raise S.StoryError("сначала биты: story_continue (после сценария)")
    world = S.get_world(store, int(ep["world_id"]))
    library = S.list_scenes(store, int(world["id"]))
    by_i = {int(b["i"]): b for b in beats}
    problems = []
    options = None
    for ch in changes or []:
        if not isinstance(ch, dict) or ch.get("i") is None or int(ch["i"]) not in by_i:
            problems.append(f"нет бита {ch.get('i') if isinstance(ch, dict) else ch!r} (0–{len(beats) - 1})")
            continue
        beat = by_i[int(ch["i"])]
        if ch.get("scene_id"):
            scene = S.get_scene(store, int(ch["scene_id"]))
            if int(scene["world_id"]) != int(world["id"]):
                problems.append(f"бит {beat['i']}: сцена {scene['id']} другого мира")
                continue
            beat["scene_id"] = int(scene["id"])
        elif isinstance(ch.get("new_scene"), dict):
            spec = dict(ch["new_scene"])
            spec["cast"] = spec.get("cast") or spec.get("characters") or []
            hit, _sim = S.match_scene(library, {**spec, "location_id": spec.get("location_id") or 0})
            scene = hit or S.save_scene(store, int(world["id"]), location_id=spec.get("location_id") or 0,
                                        cast=spec["cast"], action=spec.get("action"), accent=spec.get("accent") or "",
                                        tags=spec.get("tags") or [], title=spec.get("title") or P._text(beat["text"], 60),
                                        origin="agent")
            if hit is None:
                library.append(scene)
            beat["scene_id"] = int(scene["id"])
        if ch.get("camera"):
            if ch["camera"] not in CAMERA_MOVES:
                problems.append(f"бит {beat['i']}: camera — {', '.join(CAMERA_MOVES)}")
                continue
            beat["camera"] = ch["camera"]
        if "sfx" in ch:
            tag = P._text(ch.get("sfx"), 120)
            if not tag:
                beat["sfx"] = None
            else:
                options = options if options is not None else S.sfx_options(store, int(world["project_id"]))
                hit = S.resolve_sfx(tag, options)
                if hit is None:
                    problems.append(f"бит {beat['i']}: записи «{tag}» нет (паки, «Файлы для монтажа» с тегом «звук») — "
                                    "add_sound_from_url по ссылке владельца")
                    continue
                beat["sfx"] = {"tag": tag, "ref": hit["ref"], "label": hit["label"]}
    if problems:
        raise S.StoryError("биты не приняты: " + "; ".join(problems))
    _decorate(store, int(world["id"]), beats)
    S.set_status(store, episode_id, "planned", None, beats=beats, error="", clip_id=None, timeline={})
    return S.get_episode(store, episode_id)


# --------------------------------------------------------------------------- #
# 3–4. Деньги и генерация
# --------------------------------------------------------------------------- #


def estimate(store: Any, episode_id: int) -> dict:
    """Что ещё нужно эпизоду и сколько стоит (бесплатно, ничего не запускает)."""
    ep = S.get_episode(store, episode_id)
    est = G.estimate(store, int(ep["project_id"]), S.STAGE, None, [S.episode_group(episode_id)])
    kinds: dict[str, list[dict]] = {}
    for item in est["items"]:
        kinds.setdefault(item["ref_kind"], []).append(item)
    scenes = kinds.get(S.SCENE_KIND, [])
    tts = kinds.get(S.NARRATION_KIND, [])
    used = set(S.beat_scene_ids(ep))
    images = [i for i in est["items"] if i["kind"] == "image"]
    return {"episode_id": int(episode_id), "total_rub": est["total_rub"], "paid": est["paid"],
            "beats": len(ep.get("beats") or []), "scenes_used": len(used), "new_scenes": len(scenes),
            "reused_scenes": max(0, len(used) - len(scenes)), "sheets": len(kinds.get(S.SHEET_KIND, [])),
            "plates": len(kinds.get(S.PLATE_KIND, [])), "style_frames": len(kinds.get(S.STYLE_KIND, [])),
            "images": len(images), "images_rub": round(sum(i["estimate_rub"] for i in images), 2),
            "narration_rub": round(sum(i["estimate_rub"] for i in tts), 2), "narration_ready": not tts,
            "cap_rub": float(settings.stories_episode_cap_rub),
            "items": [{k: i.get(k) for k in ("label", "kind", "ref_kind", "provider", "model", "estimate_rub",
                                             "price_note")} for i in est["items"]],
            "problems": est["problems"], "budget": est["budget"], "note": est["note"]}


def request_generation(store: Any, episode_id: int, requested_by: str = "stories") -> dict:
    """Бесплатное (Gemini TTS, сухой прогон) — сразу; платное — одна карточка владельцу."""
    ep = S.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    group = S.episode_group(episode_id)
    narr = G.estimate(store, pid, S.STAGE, None, [f"{group}:{S.NARRATION_KIND}"])
    if narr["items"] and not narr["paid"]:
        G.request_stage(store, pid, S.STAGE, None, [f"{group}:{S.NARRATION_KIND}"], requested_by=requested_by)
    est = estimate(store, episode_id)
    if not est["items"]:
        S.set_status(store, episode_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
        return {"status": "ready", "estimate": est}
    note = f"завязка: {P._text(ep['premise'], 200)}"
    try:
        req = G.request_stage(store, pid, S.STAGE, None, [group], requested_by=requested_by, note=note)
    except G.BudgetError as exc:
        S.set_status(store, episode_id, "failed", ("planned",), error=str(exc)[:600])
        return {"status": "failed", "error": str(exc), "estimate": est}
    if req["status"] == "awaiting_approval":
        S.set_status(store, episode_id, "awaiting_approval", ("planned",), approval_id=req["approval_id"],
                     estimate_rub=est["total_rub"])
        return {"status": "awaiting_approval", "approval_id": req["approval_id"], "estimate": est, "note": req["note"]}
    S.set_status(store, episode_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
    return {"status": "generating", "estimate": est, "run": req.get("run")}


def readiness(store: Any, episode_id: int) -> dict:
    """Готово ли всё к сборке: озвучка и картинка у каждой сцены битов; что упало."""
    ep = S.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    missing, failed, active, held = [], [], [], []
    checks = []
    if S.narration_ref(store, ep) is None:
        checks.append((S.T_EP, int(episode_id), S.NARRATION_KIND, "озвучка"))
    for sid in sorted(set(S.beat_scene_ids(ep))):
        scene = S.get_scene(store, sid)
        if not scene["ready"]:
            checks.append((S.T_SCENE, sid, S.SCENE_KIND, f"сцена «{P._text(scene['title'], 40)}»"))
    for target_type, target_id, kind, label in checks:
        job = X._target_jobs(store, pid, target_type, target_id, kind)
        missing.append(label)
        if job is None:
            continue
        if job["status"] in ("failed", "blocked_policy", "uncertain", "cancelled"):
            failed.append(f"{label}: {job['status']} — {(job.get('error') or '')[:160]}")
        elif job["status"] == "held":
            held.append(f"{label}: {(job.get('error') or 'держится')[:160]}")
        elif job["status"] in ("approved", "submitting", "polling", "recovering", "awaiting"):
            active.append(label)
    return {"ready": not missing, "missing": missing, "failed": failed, "active": active, "held": held}


def check_generation(store: Any, episode_id: int) -> str:
    """awaiting_approval/generating → дальше. «wait» — ждём владельца или запуски."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "awaiting_approval" and ep.get("approval_id"):
        card = A.get_approval(store, int(ep["approval_id"]))
        if card["status"] == "pending":
            return "wait"
        if card["status"] != "approved":
            S.set_status(store, episode_id, "cancelled", ("awaiting_approval",),
                         error=f"карточка #{card['id']}: {card['status']}"
                               + (f" — {card.get('decision_comment')}" if card.get("decision_comment") else ""))
            return "cancelled"
        S.set_status(store, episode_id, "generating", ("awaiting_approval",))
    state = readiness(store, episode_id)
    if state["ready"]:
        S.set_status(store, episode_id, "assembling", ("generating", "awaiting_approval"))
        return "ready"
    if state["active"]:
        return "wait"
    if state["held"] and not state["failed"]:
        S.update_episode(store, episode_id, error="ждёт запуска: " + "; ".join(state["held"][:2])
                         + " — включите генерацию и нажмите «Продолжить» у карточки")
        return "wait"
    if state["failed"]:
        S.set_status(store, episode_id, "failed", ("generating", "awaiting_approval"),
                     error="генерация не удалась: " + "; ".join(state["failed"])[:600])
        return "failed"
    S.set_status(store, episode_id, "planned", ("generating", "awaiting_approval"))
    return "replan"


# --------------------------------------------------------------------------- #
# 5. Слова, время битов, сборка
# --------------------------------------------------------------------------- #


def transcribe_words(path: Path) -> list[dict]:
    """Слова озвучки с временем (Whisper large-v3-turbo). Тесты подменяют."""
    return X.transcribe_words(path)


def place(beats: list[dict], words: list[dict], total: float) -> list[dict]:
    """Биты по времени: старт — начало первого слова бита; слипшиеся (< 0.35 с) — в прошлый; бит длиннее
    ``REAL_SPLIT_SEC`` делится той же сценой с другим движением камеры."""
    if not words:
        raise S.StoryError("нет слов озвучки")
    firsts = X.align(beats, words)
    placed: list[dict] = []
    for n, beat in enumerate(beats):
        start = 0.0 if n == 0 else round(float(words[firsts[n]]["start"]), 3)
        w0 = 0 if n == 0 else firsts[n]
        if placed and start - placed[-1]["start"] < MIN_PLACED_SEC:
            placed[-1]["text"] += " " + beat["text"]
            if beat.get("sfx") and not placed[-1].get("sfx"):
                placed[-1]["sfx"] = beat["sfx"]
            continue
        placed.append({**beat, "start": start, "w0": w0})
    for n, item in enumerate(placed):
        item["end"] = placed[n + 1]["start"] if n + 1 < len(placed) else round(total, 3)
        item["w1"] = (placed[n + 1]["w0"] - 1) if n + 1 < len(placed) else len(words) - 1
    out: list[dict] = []
    for item in placed:
        length = item["end"] - item["start"]
        inner = [k for k in range(item["w0"] + 1, item["w1"] + 1)
                 if item["start"] + 1.0 < words[k]["start"] < item["end"] - 1.0]
        if length > REAL_SPLIT_SEC and inner and item is not placed[-1]:
            mid = item["start"] + length / 2
            k = min(inner, key=lambda j: abs(words[j]["start"] - mid))
            cut = round(float(words[k]["start"]), 3)
            out += [{**item, "end": cut, "w1": k - 1},
                    {**item, "start": cut, "w0": k, "variant": "alt", "sfx": None, "split_of": item["i"],
                     "camera": CAMERA_ALT.get(item.get("camera") or "push_in", "push_in")}]
        else:
            out.append(item)
    return out


def _sfx_duration(store: Any, ref: str) -> float:
    if ref.isdigit():
        try:
            return float(store.get_montage_asset(int(ref)).get("duration_sec") or 1.5)
        except KeyError:
            return 1.5
    try:
        from app.audio_tools import _sound_ok

        ok, dur = _sound_ok(ref)
        return float(dur) if ok else 1.5
    except Exception:  # noqa: BLE001
        return 1.5


def _captions_props(world: dict) -> dict:
    cap = world["captions"]
    return {"style": "mrbeast", "font": "Rubik", "weight": 900, "size": int(cap["size"]), "color": cap["color"],
            "activeColor": cap["active"], "stroke": {"color": cap["stroke"], "width": 12},
            "uppercase": bool(cap["uppercase"]), "maxWords": 1, "maxLines": 1, "animation": "pop",
            "wordScale": 1.06, "punctuation": False}


def build_layers(store: Any, world: dict, ep: dict, placed: list[dict], narration_asset: int, total: float,
                 notes: list[str]) -> tuple[list[dict], list[dict]]:
    """Слои (сцены с камерой, звуки действий, озвучка, субтитры по слову, название) и ключи 3D-камеры."""
    from app.aivideo_assemble import CAMERA_PRESET_MOVES, _keys, preset_camera
    from app.camera3d import PresetError

    scenes = {int(s["id"]): s for s in S.list_scenes(store, int(world["id"]))}
    layers: list[dict] = []
    camera: list[dict] = []
    for n, item in enumerate(placed):
        scene = scenes.get(int(item["scene_id"]))
        if scene is None or not scene["ready"]:
            raise S.StoryError(f"бит {item['i']}: у сцены {item['scene_id']} нет картинки")
        t0, t1 = item["start"], item["end"]
        move = item.get("camera") or "push_in"
        base, keys = _keys(move, t1 - t0)
        layer = {"id": f"{LAYER_PREFIX}sc{n:02d}", "type": "image", "name": f"Сцена: {scene['title']}"[:60],
                 "start": round(t0, 3), "end": round(t1, 3), "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                 "scale": base, "props": {"asset_id": int(scene["asset_id"]), "w": 1080, "content": True}}
        if keys:
            layer["keys"] = keys
        layers.append(layer)
        if move in CAMERA_PRESET_MOVES:
            try:
                camera = preset_camera(move, t0, t1, total, camera, layers, n + 1 < len(placed))
            except PresetError as exc:
                notes.append(f"бит {item['i']}: камера {move} не встала ({exc}) — остался Ken Burns")
        sfx = item.get("sfx")
        if sfx and sfx.get("ref"):
            s = round(min(t1 - 0.2, t0 + SFX_OFFSET_SEC), 3)
            dur = min(SFX_MAX_SEC, max(0.4, _sfx_duration(store, str(sfx["ref"]))))
            layers.append({"id": f"{LAYER_PREFIX}sfx{n:02d}", "type": "audio", "name": f"Звук: {sfx.get('label') or ''}"[:60],
                           "start": max(0.0, s), "end": round(min(total, s + dur), 3),
                           "props": {"asset_id": str(sfx["ref"]), "mode": "track", "volume": SFX_VOLUME,
                                     "fadeOut": 0.15, "role": "sfx"}})
    options = ep.get("options") or {}
    disaster = D.is_disaster(world, ep)
    speech_end = float(ep.get("_speech_end") or total)
    if disaster:
        # Название серии поверх первого кадра, HUD-плашки фраз, титр следующей части после последнего слова.
        from app.aivideo_gen import _asset_path

        stroke = world["captions"]["stroke"]
        if options.get("title_card", True):
            layers += D.title_layers(ep, placed, stroke, LAYER_PREFIX)
        used = {int(p["scene_id"]) for p in placed}
        paths = {sid: _asset_path(store, {"asset_id": sc.get("asset_id")}) for sid, sc in scenes.items() if sid in used}
        layers += D.hud_layers(ep, placed, speech_end, float(world["captions"]["y"]), paths, LAYER_PREFIX)
        if total - speech_end >= 0.5:
            layers.append(D.part_layer(ep, speech_end, total, stroke, LAYER_PREFIX))
    elif options.get("title_card"):
        title = P._text(ep.get("title") or ep["premise"], 60)
        end = round(min(TITLE_SEC, placed[0]["end"] if placed else TITLE_SEC, total), 3)
        # Дорожка выше сцен (1) и ниже субтитров (6): на дорожке 0 название пряталось под картинкой.
        layers.append({"id": f"{LAYER_PREFIX}title", "type": "text", "name": "Название", "start": 0.0, "end": end,
                       "track": 5, "x": 0.5, "y": 0.16, "props": {"text": title, "font": "Rubik", "weight": 900, "size": 84,
                                                      "color": "#ffffff", "align": "center",
                                                      "stroke": {"color": world["captions"]["stroke"], "width": 10}},
                       "in": {"type": "pop", "duration": 0.2}})
    layers.append({"id": f"{LAYER_PREFIX}narr", "type": "audio", "name": "Рассказчик", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(narration_asset), "mode": "track", "volume": 1.0, "role": "voice"}})
    layers.append({"id": f"{LAYER_PREFIX}caps", "type": "captions", "name": "Субтитры по слову", "start": 0.0,
                   "end": round(speech_end if disaster else total, 3), "track": 6, "x": 0.5, "y": float(world["captions"]["y"]),
                   "props": _captions_props(world)})
    return layers, camera


def _music_track(store: Any, world: dict, ep: dict) -> int | None:
    if D.music_off(world, ep):
        return None
    opt = (ep.get("options") or {}).get("music_track_id")
    for value in (opt, world.get("music_track_id"), V._style_row(store, int(ep["project_id"])).get("music_track_id")):
        if value in (None, "", 0):
            continue
        try:
            store.get_audio_track(int(value))
            return int(value)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _tool(ctx: Any, name: str, args: dict, notes: list[str] | None = None) -> dict:
    try:
        return X._tool(ctx, name, args, notes)
    except V.AivError as exc:
        raise S.StoryError(str(exc)) from exc


def assemble(store: Any, episode_id: int) -> dict:
    """Озвучка → слова → биты по времени → новый холст-клип."""
    from app import agent_tools as T
    from app.aivideo_gen import _asset_path
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration
    from app.motion import normalize_camera, normalize_layers

    ep = S.get_episode(store, episode_id)
    if S.is_3d(ep):
        from app import stories_3d

        return stories_3d.assemble(store, episode_id)
    world = S.get_world(store, int(ep["world_id"]))
    ref = S.narration_ref(store, ep)
    if ref is None:
        raise S.StoryError("нет озвучки эпизода — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = list(ep.get("notes") or [])
    voice = S.world_voice(store, world)
    words: list[dict] = []
    if voice["provider"] != "none":
        try:
            words = transcribe_words(path)
        except Exception as exc:  # noqa: BLE001 - Whisper выключен/не встал: по длине текста
            notes.append(f"Whisper не сработал ({type(exc).__name__}: {str(exc)[:120]}) — слова по длине текста")
    if not words:
        words = X.proportional_words(S.narration_text(ep), audio_sec)
    disaster = D.is_disaster(world, ep)
    speech_end = round(float(words[-1]["end"]), 3)
    if disaster:
        # Конец обрывается сразу после последнего слова — дальше только титр следующей части (2 с).
        total = round(max(speech_end + D.PART_SEC, MIN_DURATION_SEC), 2)
    else:
        total = round(max(words[-1]["end"] + TAIL_SEC, audio_sec + 0.4, MIN_DURATION_SEC), 2)
    if total > base_seconds():
        raise S.StoryError(f"история {total:.1f} с длиннее холста {base_seconds():.0f} с")
    placed = place(ep["beats"], words, total)
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": ["#101014", "#101014"]},
                              (ep.get("title") or ep["premise"])[:100],
                              f"История ({world['name']}): {ep['premise']}"[:500])
    clip_id = int(clip["id"])
    P.assign_plan_project(store, clip_id, int(ep["project_id"]))  # иначе холст виден только по прямой ссылке
    layers, camera = build_layers(store, world, {**ep, "_speech_end": speech_end}, placed, int(ref["asset_id"]),
                                  total, notes)
    render = dict(store.get_clip_plan(clip_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera(camera)
    store.set_clip_plan_render_settings(clip_id, render)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id,
                        on_first_change=lambda: snapshot(store, clip_id, "история: сборка"))
    _tool(ctx, "set_subtitle_words", {"words": words})
    track = _music_track(store, world, ep)
    if track:
        _tool(ctx, "set_music", {"on": True, "track_id": track})
        _tool(ctx, "set_music_duck", {"duck": X._speech_spans(words)})
    elif D.music_off(world, ep):
        if D.MUSIC_NOTE not in notes:
            notes.append(D.MUSIC_NOTE)
    else:
        notes.append("музыка не выбрана (у мира нет трека) — set_music по list_music")
    if (ep.get("options") or {}).get("cut_sfx"):
        result, ok = T.run_tool(ctx, "auto_sfx", {"on": ["cuts"]})
        if not ok:
            notes.append(f"вжухи на склейках не встали: {str((result or {}).get('error') or result)[:160]}")
    mixed = X.auto_mix_clip(ctx, notes)
    S.mark_used(store, [int(p["scene_id"]) for p in placed])
    sfx_count = sum(1 for p in placed if p.get("sfx"))
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2),
                "editor_url": f"/clips/{clip_id}",
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "scene_id", "scene_title", "camera",
                                                  "sfx", "variant", "sentence")} for p in placed],
                "shots_per_min": round(len(placed) / total * 60, 1), "sfx": sfx_count,
                "layers": len(render["layers"]), "camera_keys": len(render["camera"]), "music_track_id": track,
                **({"mix": mixed} if mixed else {})}
    if disaster:
        hud = [lay for lay in render["layers"] if str(lay.get("id") or "").startswith(f"{LAYER_PREFIX}hud")]
        timeline.update({"format": S.FORMAT_DISASTER, "part": D.part_of(ep), "speech_end": speech_end,
                         "part_card": D.part_card_text(ep),
                         "hud": [{"text": h["props"]["text"], "color": h["props"]["color"], "start": h["start"],
                                  "end": h["end"], "x": h["x"], "y": h["y"]} for h in hud],
                         "publish": D.publish_meta(ep)})
    S.set_status(store, episode_id, "done", ("assembling",), clip_id=clip_id, words=words, timeline=timeline,
                 notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "layers": len(render["layers"]),
           "shots_per_min": timeline["shots_per_min"], "editor_url": timeline["editor_url"], "notes": notes}
    options = ep.get("options") or {}
    if options.get("render"):
        out["publish"] = finish_publish(store, episode_id, clip_id, cover=options.get("cover", True))
    return out


def finish_publish(store: Any, episode_id: int, clip_id: int, cover: bool = True) -> dict:
    """По желанию: рендер, обложки, заголовок/описание (Gemini может не ответить — эпизод готов всё равно)."""
    from app.render import ClipRenderService

    out: dict[str, Any] = {}
    ep = S.get_episode(store, episode_id)
    notes = list(ep.get("notes") or [])
    disaster = D.is_disaster(S.get_world(store, int(ep["world_id"])), ep)
    try:
        clip = ClipRenderService(store).render_clip_plan(clip_id)
        out["render"] = {"clip": clip.get("id"), "status": clip.get("status")}
        if clip.get("status") == "succeeded" and disaster:
            # Сериал-катастрофа: обложка по шаблону формата и описание «вопрос + своя версия + теги» — без ИИ.
            from app.ai.publish_meta import save_publish_meta

            if cover:
                try:
                    out["covers"] = D.make_covers(store, episode_id, int(clip["id"]))["covers"]
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"обложки не сделаны: {str(exc)[:160]}")
            meta = (ep.get("timeline") or {}).get("publish") or D.publish_meta(ep)
            record = save_publish_meta(store, int(clip["id"]), meta, source="story")
            out["title"] = record["title"]
            out["description"] = record["description"]
        elif clip.get("status") == "succeeded":
            if cover:
                try:
                    from app.cover_variants import generate_covers

                    out["covers"] = len(generate_covers(store, int(clip["id"]), count=3).get("covers") or [])
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"обложки не сделаны: {str(exc)[:160]}")
            try:
                from app.ai.publish_meta import generate_publish_metadata, save_publish_meta

                if settings.gemini_api_key:
                    meta = generate_publish_metadata(store, int(clip["id"]))
                    save_publish_meta(store, int(clip["id"]), meta)
                    out["title"] = meta.get("title")
                else:
                    notes.append("заголовок и описание — без Gemini не сделаны")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"заголовок/описание не сделаны: {str(exc)[:160]}")
        else:
            notes.append(f"рендер: {clip.get('status')} {str(clip.get('error') or '')[:160]}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"рендер не удался: {str(exc)[:200]}")
    S.update_episode(store, episode_id, notes=notes)
    return out


# --------------------------------------------------------------------------- #
# Ведущий: advance / работник
# --------------------------------------------------------------------------- #

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
STEP_LABELS = {"new": "сценарий", "scripted": "подбор сцен", "planned": "оценка", "awaiting_approval": "одобрение",
               "generating": "генерация", "assembling": "сборка"}


def _lock(episode_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(episode_id), threading.Lock())


def advance(store: Any, episode_id: int, client: Any = None, max_steps: int = 8) -> dict:
    """Двигать эпизод по шагам, пока не упрётся в ожидание (карточка, запуски) или конец.
    Ошибка шага — ``failed`` с текстом (продолжить — ``resume``/``story_continue``)."""
    lock = _lock(episode_id)
    if not lock.acquire(blocking=False):
        return {**S.get_episode(store, episode_id, full=False), "busy": True}
    try:
        for _ in range(max_steps):
            status = S.get_episode(store, episode_id, full=False)["status"]
            try:
                if status == "new":
                    write_script(store, episode_id, client=client)
                elif status == "scripted":
                    plan_beats(store, episode_id)
                elif status == "planned":
                    if request_generation(store, episode_id)["status"] in ("awaiting_approval", "failed"):
                        break
                elif status in ("awaiting_approval", "generating"):
                    if check_generation(store, episode_id) in ("wait", "failed", "cancelled"):
                        break
                elif status == "assembling":
                    if (assemble(store, episode_id) or {}).get("wait"):
                        break  # 3D-серия ждёт рендер на компьютере (очередь хост-воркера)
                else:
                    break
            except Exception as exc:  # noqa: BLE001 - шаг упал: эпизод ждёт правки
                _log.warning("story %s step %s failed: %s", episode_id, status, exc)
                S.set_status(store, episode_id, "failed", None,
                             error=f"{STEP_LABELS.get(status, status)}: {str(exc)[:500]}")
                break
    finally:
        lock.release()
    return S.get_episode(store, episode_id)


def resume(store: Any, episode_id: int, client: Any = None) -> dict:
    """Продолжить упавший/отменённый эпизод с того шага, где есть данные."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] in ("failed", "cancelled"):
        if not S.narration_text(ep):
            status = "new"
        elif not ep.get("beats"):
            status = "scripted"
        else:
            status = "planned"
        S.set_status(store, episode_id, status, ("failed", "cancelled"), error="")
    return advance(store, episode_id, client=client)


def reassemble(store: Any, episode_id: int) -> dict:
    ep = S.get_episode(store, episode_id, full=False)
    if ep["status"] != "done":
        raise S.StoryError("пересобрать можно готовый эпизод (status done)")
    S.set_status(store, episode_id, "assembling", ("done",))
    return advance(store, episode_id)


def cancel(store: Any, episode_id: int) -> dict:
    ep = S.get_episode(store, episode_id, full=False)
    if ep.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'эпизод отменён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(ep["approval_id"]),))
        try:
            card = A.get_approval(store, int(ep["approval_id"]))
            if card["status"] == "pending":
                A._update(store, card["id"], status="expired", decision_comment="эпизод отменён")
        except KeyError:
            pass
    S.set_status(store, episode_id, "cancelled", None, error="отменено")
    return S.get_episode(store, episode_id)


def create(store: Any, world_id: int, premise: str, duration: Any = None, tone: str = "", script: dict | None = None,
           options: dict | None = None, requested_by: str = "agent", run: bool = False, client: Any = None) -> dict:
    """Новый эпизод. ``script`` — свой сценарий (без Gemini). ``run`` — сразу вести по шагам (иначе работник)."""
    ep = S.create_episode(store, world_id, premise, duration, tone, options, requested_by)
    if script:
        set_script(store, int(ep["id"]), script, source=requested_by)
    if run:
        return advance(store, int(ep["id"]), client=client)
    kick()
    return S.get_episode(store, int(ep["id"]))


_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class StoriesWorker:
    """Фон: ведёт эпизоды по шагам (сценарий, сцены, карточка, ожидание, сборка). Платные запуски
    выполняет работник ИИ-видео после «Разрешить». ``STORIES_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 5

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        global _wake
        if os.getenv("STORIES_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="stories", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        marks = ", ".join("?" for _ in S.ACTIVE_STATUSES)
        rows = self.store.db.query_all(f"SELECT id FROM story_episodes WHERE status IN ({marks}) ORDER BY id",
                                       S.ACTIVE_STATUSES)
        done = []
        for row in rows:
            advance(self.store, int(row["id"]))
            done.append(int(row["id"]))
        return done

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("stories worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()
