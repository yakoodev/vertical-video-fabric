"""ИИ-видео: сценарий и шот-лист серии от Gemini (бесплатно — ключ владельца, без Polza).

Gemini получает бриф проекта (люди, арки, шутки, табу), правила владельца, стиль-библию,
персонажей (id, имя, карта, облики), локации и цель серии, и отвечает строгим JSON
(``responseSchema``): название, логлайн, при необходимости новые персонажи/локации и планы
(длительность, камера, кто в каком облике, где, действие, реплики по говорящим).

Сервис держит рамки: план 2–8 с, не больше двух персонажей в кадре, реплики ~14 символов
в секунду. Ответ сохраняется как обычный шот-лист — его можно править (``aiv_update_script`` /
``aiv_update_shot``). Gemini недоступен (нет ключа, гео-блок) — сценарий пишет сам агент тем же
``aiv_update_script`` (схема та же, см. docs/guides/ai-video.md).
"""

from __future__ import annotations

import json
from typing import Any

from app import aivideo as V
from app import projects as P
from app.settings import settings

RULES = """\
Ты сценарист коротких вертикальных мультсериалов (Shorts/TikTok/Reels, 9:16). Пиши по-русски.
Серия — это «моушн-комикс»: на каждый план рисуется ОДИН ключевой кадр, потом камера едет по нему
(наезд, панорама, параллакс), звучат реплики персонажей. Поэтому:
- план 3–8 секунд (не короче 2), каждая реплика укладывается в свой план (~14 символов в секунду);
- в кадре не больше двух персонажей; толпы и мелкие лица не годятся;
- первый план — крючок: в первые 2 секунды зритель понимает конфликт;
- действие в кадре — одно ясное положение тела/эмоция (это стоп-кадр), а не цепочка событий;
- camera.move: static | push_in | pull_out | pan_left | pan_right | tilt_up | tilt_down | parallax_push |
  orbit | float | dolly_zoom | tilt_reveal; camera.shot: wide | full | medium | medium_close | close | extreme_close;
- characters[].id и dialogue[].who — id персонажей проекта (или new_characters); location — id локации
  (или new_locations); look — id облика персонажа, если он не в обычном виде;
- реплики живые и разговорные, без пересказа того, что и так видно; закадровый текст — who "narrator";
- у каждого персонажа поле speech — как он «говорит», соблюдай его СТРОГО:
  voice — говорит словами;
  sfx — НЕ говорит словами (робот, зверёк, малыш): его реплика станет звуком (писк, поп, тромбон),
  поэтому text — короткое звукоподражание или ремарка («Бип-бип?..», «пищит радостно»), а emotion
  обязательна (happy, sad, angry, surprised, scared, curious) — по ней подбирается звук; смысл,
  который нужен зрителю, передай действием в кадре или репликой другого персонажа;
  none — молчит: реплик ему НЕ давай вообще;
  notes персонажа — тоже правила (например «не говорит, только пищит»);
- конец — панчлайн или крючок на следующую серию.
Новых персонажей и локации заводи, только если без них никак: карта личности нового персонажа —
возраст, волосы, глаза, рост и пропорции, 2–3 цвета в hex, отличительная деталь; speech — voice | sfx | none.
Верни ТОЛЬКО JSON по схеме.
"""


def script_schema() -> dict:
    shot = {
        "type": "object",
        "properties": {
            "duration": {"type": "number"},
            "location": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "look": {"type": "string"}, "action": {"type": "string"}},
                "required": ["id"]}},
            "camera": {"type": "object", "properties": {
                "shot": {"type": "string"}, "angle": {"type": "string"}, "move": {"type": "string"},
                "lens": {"type": "number"}}, "required": ["move"]},
            "action": {"type": "string"},
            "dialogue": {"type": "array", "items": {"type": "object", "properties": {
                "who": {"type": "string"}, "text": {"type": "string"}, "emotion": {"type": "string"}},
                "required": ["who", "text"]}},
            "motion_prompt": {"type": "string"},
            "needs_motion": {"type": "boolean"},
            "transition_in": {"type": "string"},
        },
        "required": ["duration", "camera", "action", "dialogue"],
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "logline": {"type": "string"},
            "new_characters": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "name": {"type": "string"}, "identity_card": {"type": "string"},
                "speech": {"type": "string", "enum": list(V.SPEECH_MODES)}},
                "required": ["id", "name", "identity_card"]}},
            "new_locations": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["id", "name", "description"]}},
            "shots": {"type": "array", "items": shot},
        },
        "required": ["title", "logline", "shots"],
    }


def script_context(store: Any, script_id: int, brief: str | None = None) -> dict:
    script = V.get_script(store, script_id, with_shots=False)
    project_id = int(script["project_id"])
    style = V.get_style(store, project_id)
    lore = P.get_brief(store, project_id).get("brief") or {}
    rules = [{"kind": r["kind"], "text": r["text"]} for r in P.list_rules(store, project_id=project_id)]
    target = float(script["duration_target"])
    return {
        "series": P.get_project(store, project_id, include_sources=False)["name"],
        "style": {"kind": style["style_label"], "passport": style["passport_text"], "palette": style["palette"]},
        "lore": {k: lore.get(k) for k in ("summary", "people", "arcs", "jokes", "taboo") if lore.get(k)},
        "owner_rules": rules,
        "characters": [{"id": c["slug"], "name": c["name"], "identity_card": c["identity_card"],
                        "speech": c["speech"], "speech_rule": V.SPEECH_LABELS[c["speech"]],
                        **({"notes": c["notes"]} if c.get("notes") else {}),
                        "looks": [{"id": l["slug"], "name": l["name"], "outfit": l["outfit_text"], "beat": l["beat"]}
                                  for l in c["looks"]]} for c in V.list_characters(store, project_id)],
        "locations": [{"id": l["slug"], "name": l["name"], "description": l["description"]}
                      for l in V.list_locations(store, project_id)],
        "episode": {"number": script["episode_no"], "brief": brief if brief is not None else script["brief"],
                    "duration_target_sec": target,
                    "shots_hint": f"{max(4, min(15, round(target / 5)))} планов примерно по 4–6 с"},
        "previous_episodes": [{"no": s["episode_no"], "title": s["title"], "logline": s["logline"]}
                              for s in V.list_scripts(store, project_id) if s["id"] != script["id"]][-5:],
    }


def write_script(store: Any, script_id: int, brief: str | None = None, client: Any = None) -> dict:
    """Gemini пишет сценарий и шот-лист серии; новые персонажи/локации заводятся, шот-лист заменяется."""
    from app.ai.gemini import GeminiClient, _extract_response_text

    script = V.get_script(store, script_id, with_shots=False)
    if brief is not None:
        V.update_script(store, script_id, {"brief": brief})
    if client is None and not settings.gemini_api_key:
        raise V.AivError("Gemini не настроен (GEMINI_API_KEY) — напиши сценарий сам: aiv_update_script(shots=[…]) "
                         "по схеме из docs/guides/ai-video.md")
    context = script_context(store, script_id, brief)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": RULES},
                                                {"text": json.dumps(context, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json",
                             "responseSchema": script_schema()},
    }
    response = (client or GeminiClient()).generate_content(settings.gemini_text_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise V.AivError("Gemini вернул не-JSON — повтори или напиши сценарий сам") from exc
    return apply_script(store, script_id, data, source="gemini")


def apply_script(store: Any, script_id: int, data: dict, source: str = "agent") -> dict:
    """Ответ сценариста (Gemini или агент) → новые персонажи/локации + шот-лист."""
    script = V.get_script(store, script_id, with_shots=False)
    project_id = int(script["project_id"])
    created = {"characters": [], "locations": []}
    for c in data.get("new_characters") or []:
        slug = V.slugify(c.get("id") or c.get("name"), "hero")
        if V.character_by_slug(store, project_id, slug) or not c.get("identity_card"):
            continue
        V.save_character(store, project_id, name=c.get("name") or slug, identity_card=c["identity_card"], slug=slug,
                         speech=c.get("speech") if c.get("speech") in V.SPEECH_MODES else None)
        created["characters"].append(slug)
    for loc in data.get("new_locations") or []:
        slug = V.slugify(loc.get("id") or loc.get("name"), "place")
        if V.location_by_slug(store, project_id, slug):
            continue
        V.save_location(store, project_id, name=loc.get("name") or slug, description=loc.get("description") or "",
                        slug=slug)
        created["locations"].append(slug)
    out = V.replace_shots(store, script_id, data.get("shots") or [], data.get("title"), data.get("logline"))
    out["created"] = created
    out["source"] = source
    if created["characters"] or created["locations"]:
        out.setdefault("warnings", []).append(
            "заведены новые персонажи/локации — проверь их карты и сделай для них референсы (этап refs)")
    return out
