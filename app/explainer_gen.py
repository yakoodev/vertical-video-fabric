"""Объяснялки: конвейер «тема → готовый ролик» (docs/guides/explainer.md).

Шаги ролика (``expl_videos.status``), каждый повторяем и идемпотентен; ведёт ``advance`` (работник
``ExplainerWorker`` или агент ``explainer_continue``):

1. ``new`` → **сценарий** (Gemini, бесплатно): крючок в первые 2 с, вступление, 3–5 пунктов,
   призыв; русский, короткие фразы, ~15 символов речи в секунду. Gemini недоступен — ``failed`` с
   подсказкой: агент пишет текст сам (``explainer_set_script``).
2. ``scripted`` → **биты**: текст режется по фразам и частям фраз на биты по 1.5–3 с (оценка по
   длине текста; после озвучки — по настоящим словам). На каждый бит Gemini выбирает позу из
   библиотеки маскота (кандидаты — по тегам) или описывает новую; плюс чипы (числа/время) и
   ключевые слова субтитров. Плашки «#1/#2/#3» — по пунктам сценария, без Gemini. Новая поза,
   почти совпадающая с готовой (``similar_pose``), не рисуется. Gemini недоступен — позы по тегам
   (без новых).
3. ``planned`` → **оценка и деньги**: бесплатная озвучка (Gemini TTS / сухой прогон) — сразу;
   новые позы и платная озвучка — одна карточка владельцу (этап ``explainer`` машинерии ИИ-видео:
   «Разрешить», потолки, журнал трат). Ничего нового — сразу дальше.
4. ``awaiting_approval`` / ``generating`` → ждём «Разрешить» и запуски (работник ИИ-видео).
5. ``assembling`` → **сборка**: слова озвучки (Whisper large-v3-turbo; сухой прогон — по длине
   текста) → биты по времени (длинные > 3.6 с делятся тем же кадром с наездом) → холст-клип как
   пилот 887: позы слоями ``image`` с чередованием pop/zoom, вжухи на склейках, плашки с «динь»,
   чипы с «поп», слой субтитров (hormozi, крупно, капсом, ключевые слова — ``emph``), музыка
   маскота под голосом тише. По желанию (``options.render``) — рендер, обложки, заголовок/описание.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import aivideo_gen as G
from app import autopilot as A
from app import explainer as E
from app import projects as P
from app.settings import settings

_log = logging.getLogger(__name__)

CPS = 15.0  # символов русской речи в секунду (пилот: 680 символов за 44 с)
BEAT_MIN_SEC = 1.5
BEAT_MAX_SEC = 3.0
BEAT_MERGE_SEC = 0.9  # бит короче — сливается с соседним (если влезает)
REAL_SPLIT_SEC = 3.6  # после озвучки бит длиннее — делится тем же кадром с наездом
HOOK_MAX_CHARS = 32  # крючок — первые ~2 с
MAX_NEW_POSES = 20
TAIL_SEC = 0.8
# Только настоящие записи пака freesound. Пилот звал whoosh-2 и ding — это алиасы пака
# (app/motion_assets.FREESOUND_ALIASES): whoosh-soft и pop-soft; здесь — настоящие имена.
CUT_SFX = ("sfx:freesound/whoosh-1.ogg", "sfx:freesound/whoosh-soft.ogg")
BADGE_SFX = "sfx:freesound/pop-soft.ogg"
CHIP_SFX = "sfx:freesound/pop.ogg"
WHOOSH_VOLUME = 0.35
DING_VOLUME = 0.45
POP_VOLUME = 0.3
MUSIC_DUCK_DB = -8.0
POP_SEC = 0.18
BADGE_MAX_SEC = 2.0
CHIP_SEC = 1.3
PUNCH_SCALE = 1.14
LAYER_BATCH = 28

# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def gemini_client() -> Any:
    """Клиент Gemini или None (нет ключа). Тесты подменяют."""
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=120)


def _ask(client: Any, rules: str, context: dict, schema: dict, temperature: float = 0.8) -> dict:
    from app.ai.gemini import _extract_response_text

    payload = {"contents": [{"role": "user", "parts": [{"text": rules},
                                                       {"text": json.dumps(context, ensure_ascii=False)}]}],
               "generationConfig": {"temperature": temperature, "responseMimeType": "application/json",
                                    "responseSchema": schema}}
    response = client.generate_content(settings.gemini_text_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise E.ExplError("Gemini вернул не-JSON") from exc
    if not isinstance(data, dict):
        raise E.ExplError("Gemini вернул не объект")
    return data


# --------------------------------------------------------------------------- #
# 1. Сценарий
# --------------------------------------------------------------------------- #

SCRIPT_RULES = """\
Ты сценарист вертикальных роликов-объяснялок (Shorts/TikTok/Reels, 30–60 с). Маскот из context
рассказывает тему зрителю голосом за кадром (на экране он меняет позы). Пиши ПО-РУССКИ, живым
разговорным языком, на «ты», короткими фразами (до 12 слов), без канцелярита, эмодзи и хэштегов.
Структура:
- hook — крючок: ОДНА фраза до 30 символов, звучит за 2 секунды; вопрос-боль или неожиданный факт
  («Спишь восемь часов, а встаёшь разбитым?»);
- intro — одна короткая фраза: что будет («Есть три ошибки, которые крадут твой сон.»), можно пусто;
- points — 3–5 пунктов (сколько просит тема и длина): title — фраза-заголовок пункта, начинается
  с номера словами («Ошибка номер один: телефон в кровати.», «Совет номер два: …»); body — 2–3
  короткие фразы: почему это важно и что конкретно сделать;
- cta — призыв в конце (результат + «подпишись…»), одна-две фразы.
Объём всего текста — примерно duration_sec × 15 символов (±10%). Числа и время пиши словами
(«в семь», «до двух часов дня») — так их правильно прочитает озвучка. Ничего не выдумывай про
здоровье и деньги без меры: только общеизвестные безопасные советы, без обещаний сумм и дозировок.
Соблюдай правила владельца (owner_rules) и тон (tone). title — название ролика до 60 символов.
Верни ТОЛЬКО JSON по схеме.
"""


def script_schema() -> dict:
    return {"type": "object", "properties": {
        "title": {"type": "string"}, "hook": {"type": "string"}, "intro": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"]}},
        "cta": {"type": "string"}},
        "required": ["title", "hook", "points", "cta"]}


def script_context(store: Any, video: dict) -> dict:
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    project_id = int(video["project_id"])
    rules = [{"kind": r["kind"], "text": r["text"]} for r in P.list_rules(store, project_id=project_id)]
    project = P.get_project(store, project_id, include_sources=False)
    previous = [v["topic"] for v in E.list_videos(store, project_id, int(mascot["id"]), limit=12)
                if int(v["id"]) != int(video["id"])]
    return {"channel": project["name"], "channel_about": project.get("description") or "",
            "mascot": {"name": mascot["name"], "persona": mascot["persona"]},
            "topic": video["topic"], "duration_sec": video["duration_target"], "tone": video["tone"],
            "target_chars": int(video["duration_target"] * CPS), "owner_rules": rules,
            "previous_topics": previous}


def _sentence(text: Any) -> str:
    text = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def normalize_script(data: dict) -> dict:
    """Сценарий (Gemini или агент) → разделы и полный текст. ``text`` без пунктов — один раздел."""
    sections: list[dict] = []
    if P._text(data.get("hook")):
        sections.append({"kind": "hook", "text": _sentence(data["hook"])})
    if P._text(data.get("intro")):
        sections.append({"kind": "intro", "text": _sentence(data["intro"])})
    points = [p for p in data.get("points") or [] if isinstance(p, dict) and (p.get("title") or p.get("body"))]
    for n, point in enumerate(points[:6], 1):
        title = _sentence(point.get("title"))
        body = " ".join(_sentence(s) for s in re.split(r"(?<=[.!?…])\s+", str(point.get("body") or "")) if s.strip())
        sections.append({"kind": "point", "n": n, "title": title, "text": " ".join(x for x in (title, body) if x)})
    if P._text(data.get("cta")):
        sections.append({"kind": "cta", "text": _sentence(data["cta"])})
    if not sections and P._text(data.get("text")):
        sections.append({"kind": "body", "text": " ".join(str(data["text"]).split())})
    if not sections:
        raise E.ExplError("пустой сценарий: нужны hook, points [{title, body}], cta (или text)")
    text = " ".join(s["text"] for s in sections)
    warnings = []
    if sections[0]["kind"] == "hook" and len(sections[0]["text"]) > HOOK_MAX_CHARS + 8:
        warnings.append(f"крючок {len(sections[0]['text'])} символов — дольше 2 с, сократи")
    if points and not 2 <= len(points) <= 6:
        warnings.append(f"пунктов {len(points)} — лучше 3–5")
    return {"title": P._text(data.get("title"), 100), "sections": sections, "text": text,
            "points": len(points), "chars": len(text), "est_sec": round(len(text) / CPS, 1), "warnings": warnings}


def write_script(store: Any, video_id: int, client: Any = None) -> dict:
    video = E.get_video(store, video_id)
    client = client or gemini_client()
    if client is None:
        raise E.ExplError("Gemini не настроен (GEMINI_API_KEY) — напиши текст сам: explainer_set_script(video_id, "
                          "hook, intro, points [{title, body}], cta)")
    data = _ask(client, SCRIPT_RULES, script_context(store, video), script_schema(), 0.9)
    return set_script(store, video_id, data, source="gemini")


def set_script(store: Any, video_id: int, data: dict, source: str = "agent") -> dict:
    """Сценарий ролика → разделы и текст; биты и сборка сбрасываются (дальше — подбор поз)."""
    video = E.get_video(store, video_id)
    if video["status"] in ("assembling",):
        raise E.ExplError("ролик собирается — дождись сборки")
    script = normalize_script(data or {})
    script["source"] = source
    target = float(video["duration_target"])
    if abs(script["est_sec"] - target) > target * 0.25:
        script["warnings"].append(f"текст ≈ {script['est_sec']:.0f} с при цели {target:.0f} с")
    E.set_status(store, video_id, "scripted", None, script=script, beats=[], words=[], timeline={},
                 title=script["title"] or P._text(video["topic"], 100), error="", approval_id=None, clip_id=None)
    return E.get_video(store, video_id)


# --------------------------------------------------------------------------- #
# 2. Биты и позы
# --------------------------------------------------------------------------- #


def _est(text: str) -> float:
    return len(text) / CPS


def _clauses(sentence: str) -> list[str]:
    parts = re.split(r"(?<=[,:;])\s+|\s+(?=[—–]\s)", sentence)
    return [p.strip() for p in parts if p.strip()]


def _chunk(sentence: str) -> list[str]:
    if _est(sentence) <= BEAT_MAX_SEC:
        return [sentence]
    out: list[str] = []
    cur = ""
    for clause in _clauses(sentence):
        cand = f"{cur} {clause}".strip()
        if cur and _est(cand) > BEAT_MAX_SEC and _est(cur) >= BEAT_MIN_SEC * 0.8:
            out.append(cur)
            cur = clause
        else:
            cur = cand
    if cur:
        out.append(cur)
    final: list[str] = []
    for piece in out:  # часть всё ещё длинная — по словам пополам
        while _est(piece) > BEAT_MAX_SEC * 1.3:
            words = piece.split()
            half = max(1, len(words) // 2)
            final.append(" ".join(words[:half]))
            piece = " ".join(words[half:])
        final.append(piece)
    return final


def split_beats(script: dict) -> list[dict]:
    """Разделы сценария → биты по 1.5–3 с (оценка по длине текста)."""
    beats: list[dict] = []
    for section in script.get("sections") or []:
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", section["text"]) if s.strip()]
        first = True
        for sentence in sentences:
            pieces = _chunk(sentence.strip())
            if first and section["kind"] == "point" and len(pieces) == 1:
                # «Ошибка номер один: телефон в кровати.» — как в пилоте: номер (пальцы + плашка) и
                # тема — два бита.
                head, sep, tail = sentence.strip().partition(":")
                if sep and len(head) >= 8 and len(tail.strip()) >= 8:
                    pieces = [head + sep, tail.strip()]
            for piece in pieces:
                beats.append({"text": piece, "section": section["kind"], "point": section.get("n"),
                              "point_start": section.get("n") if (first and section["kind"] == "point") else None})
                first = False
    merged: list[dict] = []
    for beat in beats:
        prev = merged[-1] if merged else None
        if (prev and _est(beat["text"]) < BEAT_MERGE_SEC and prev["section"] == beat["section"]
                and prev.get("point") == beat.get("point") and not beat.get("point_start")
                and _est(prev["text"] + " " + beat["text"]) <= BEAT_MAX_SEC + 0.5):
            prev["text"] = f"{prev['text']} {beat['text']}"
            continue
        merged.append(dict(beat))
    for i, beat in enumerate(merged):
        beat["i"] = i
        beat["est_sec"] = round(_est(beat["text"]), 2)
        beat["badge"] = f"#{beat['point_start']}" if beat.get("point_start") else None
    return merged


PLAN_RULES = """\
Ты монтажёр роликов-объяснялок. Маскот (mascot) рассказывает тему; на каждом бите (1.5–3 с) кадр
меняется — маскот в новой позе на плоском фоне. Для КАЖДОГО бита (beats) реши:
- pose_id — id позы из библиотеки (library), если она подходит по смыслу бита: эмоция, жест,
  предмет. ПЕРЕИСПОЛЬЗУЙ библиотеку: готовая поза бесплатна, новая стоит денег. candidates бита —
  лучшие позы по тегам (но можно любую из library);
- если в библиотеке нет ничего близкого по смыслу — pose_id 0 и new_pose_key: ключ новой позы из
  new_poses (одну новую позу можно дать нескольким битам). Новых — как можно меньше;
- соседние биты — РАЗНЫЕ позы;
- бит с point_start N (начало пункта N списка) — поза, где маскот показывает N пальцами
  (number_shown N); нет такой в библиотеке — новая с number_shown N;
- new_poses[]: description — ПО-АНГЛИЙСКИ одна фраза 10–30 слов, только поза: выражение лица,
  жест, 0–2 простых предмета в руках или рядом; без фона, без текста и цифр (пример: "shocked,
  eyes extremely wide, feathers bristled, a bright smartphone screen in front of it"); title — 2–4
  слова по-русски; tags — 5–10 коротких тегов по-русски и по-английски (эмоция, жест, предметы,
  тема); emotion — одно слово по-английски; number_shown — сколько пальцев показывает (или 0);
- chip — маленькая плашка, если в бите есть конкретное число или время: «07:00», «8 ч», «−30%»,
  «2 л» (до 8 символов); chip_word — слово бита, на котором она появляется. 2–5 чипов на ролик,
  пусто, если числа нет;
- emphasis — 0–2 ключевых слова бита (точно как в тексте бита): их выделит субтитр — числа,
  главное существительное или глагол.
Верни ТОЛЬКО JSON по схеме: beats — ровно столько, сколько в запросе, в том же порядке (i).
"""


def plan_schema() -> dict:
    return {"type": "object", "properties": {
        "beats": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "pose_id": {"type": "integer"}, "new_pose_key": {"type": "string"},
            "chip": {"type": "string"}, "chip_word": {"type": "string"},
            "emphasis": {"type": "array", "items": {"type": "string"}}},
            "required": ["i", "pose_id"]}},
        "new_poses": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}, "emotion": {"type": "string"},
            "number_shown": {"type": "integer"}}, "required": ["key", "description", "tags"]}}},
        "required": ["beats"]}


def _library_brief(poses: list[dict]) -> list[dict]:
    return [{"id": p["id"], "title": p["title"], "description": p["description"], "tags": p["tags"],
             **({"number_shown": p["number_shown"]} if p.get("number_shown") else {}),
             **({"not_drawn_yet": True} if not p["ready"] else {})} for p in poses]


def plan_context(store: Any, video: dict, beats: list[dict], poses: list[dict]) -> dict:
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    text_all = " ".join(b["text"] for b in beats)
    ranked = [p for p, _s in E.candidates(poses, text_all, k=80)] if len(poses) > 80 else poses
    return {"mascot": {"name": mascot["name"], "what": mascot["identity_card"], "persona": mascot["persona"]},
            "topic": video["topic"], "library": _library_brief(ranked),
            "beats": [{"i": b["i"], "text": b["text"], "section": b["section"],
                       **({"point_start": b["point_start"]} if b.get("point_start") else {}),
                       "candidates": [p["id"] for p, s in E.candidates(poses, b["text"], 6) if s > 0]}
                      for b in beats]}


_CHIP_RE = re.compile(r"\b(\d{1,2}[:.]\d{2})\b|(\d+(?:[.,]\d+)?)\s*(%|час\w*|ч\b|мин\w*|раз\w*|л\b|кг|км|шаг\w*)",
                      re.IGNORECASE)
_UNITS = {"час": "ч", "ч": "ч", "мин": "мин", "раз": "раз", "л": "л", "кг": "кг", "км": "км", "шаг": "шагов",
          "%": "%"}


def _fallback_chip(text: str) -> dict | None:
    m = _CHIP_RE.search(text)
    if not m:
        return None
    if m.group(1):
        hh, mm = re.split(r"[:.]", m.group(1))
        return {"text": f"{int(hh):02d}:{mm}", "word": m.group(1)}
    unit = next((v for k, v in _UNITS.items() if m.group(3).lower().startswith(k)), m.group(3))
    sep = "" if unit == "%" else " "
    return {"text": f"{m.group(2)}{sep}{unit}"[:8], "word": m.group(2)}


def _fallback_emphasis(text: str) -> list[str]:
    from app.word_emphasis import key_word_score

    words = [w for w in text.split() if w.strip()]
    scored = sorted(((key_word_score(w), w) for w in words), key=lambda x: -x[0])
    return [scored[0][1]] if scored and scored[0][0] > 0 else []


def match_scores(beats: list[dict], poses: list[dict]) -> list[list[float]]:
    """Очки «бит × поза» по тегам; поза с числом пальцев — только на начало своего пункта."""
    numbers = {p.get("number_shown") for p in poses if p.get("number_shown")}
    out = []
    for beat in beats:
        want = beat.get("point_start")
        row = []
        for pose in poses:
            n = pose.get("number_shown")
            if n:
                row.append(10.0 if want == n else -8.0)
            else:
                row.append(E.score(pose, beat["text"]) - (3.0 if want in numbers else 0.0))
        out.append(row)
    return out


def assign_by_tags(beats: list[dict], poses: list[dict]) -> list[int]:
    """Позы битам по тегам: максимум суммы очков, соседние — разные (Витерби); бит без совпадений —
    наименее занятая в ролике «нейтральная» поза (не повторять одну и ту же)."""
    scores = match_scores(beats, poses)
    n_p = len(poses)
    if n_p == 1:
        return [int(poses[0]["id"])] * len(beats)
    dp = [scores[0][:]]
    back: list[list[int]] = [[-1] * n_p]
    for n in range(1, len(beats)):
        prev = dp[-1]
        order = sorted(range(n_p), key=lambda k: -prev[k])
        best, second = order[0], order[1]
        row, bk = [], []
        for k in range(n_p):
            q = best if best != k else second
            row.append(scores[n][k] + prev[q])
            bk.append(q)
        dp.append(row)
        back.append(bk)
    k = max(range(n_p), key=lambda j: dp[-1][j])
    picks = [k]
    for n in range(len(beats) - 1, 0, -1):
        k = back[n][k]
        picks.append(k)
    picks.reverse()
    count: dict[int, int] = {}
    for k in picks:
        count[k] = count.get(k, 0) + 1
    for n, k in enumerate(picks):
        if scores[n][k] > 0:
            continue
        neighbors = {picks[n - 1] if n else -1, picks[n + 1] if n + 1 < len(picks) else -1}
        pool = [j for j in range(n_p) if j not in neighbors and not poses[j].get("number_shown")]
        if not pool:
            continue
        j = min(pool, key=lambda x: (count.get(x, 0), -scores[n][x], int(poses[x].get("uses") or 0), x))
        count[k] -= 1
        count[j] = count.get(j, 0) + 1
        picks[n] = j
    return [int(poses[k]["id"]) for k in picks]


def _assign_fallback(beats: list[dict], poses: list[dict]) -> list[str]:
    """Без Gemini: позы по тегам (``assign_by_tags``), без новых поз; чипы и ключевые слова — эвристикой."""
    ready = [p for p in poses if p["ready"]] or poses
    if not ready:
        raise E.ExplError("библиотека поз пуста, а Gemini недоступен — опиши позы сами: explainer_update_beats "
                          "(new_pose у каждого бита) или добавь позы (explainer_add_pose)")
    notes = ["Gemini недоступен — позы подобраны по тегам библиотеки, новых нет; поправь explainer_update_beats"]
    for beat, pose_id in zip(beats, assign_by_tags(beats, ready)):
        beat["pose_id"] = pose_id
        beat["chip"] = _fallback_chip(beat["text"])
        beat["emphasis"] = _fallback_emphasis(beat["text"])
    return notes


def _apply_gemini_plan(store: Any, mascot: dict, beats: list[dict], poses: list[dict], data: dict) -> list[str]:
    notes: list[str] = []
    by_id = {int(p["id"]): p for p in poses}
    new_specs = {}
    for spec in data.get("new_poses") or []:
        if isinstance(spec, dict) and P._text(spec.get("key")) and P._text(spec.get("description")):
            new_specs[P._text(spec["key"], 40)] = spec
    created: dict[str, int] = {}
    reused_similar: dict[str, int] = {}
    answers = {int(b.get("i", -1)): b for b in data.get("beats") or [] if isinstance(b, dict)}
    library = list(poses)
    for beat in beats:
        ans = answers.get(int(beat["i"])) or {}
        pid = int(ans.get("pose_id") or 0)
        if pid and pid not in by_id:
            notes.append(f"бит {beat['i']}: позы {pid} нет в библиотеке — подобрал по тегам")
            pid = 0
        key = P._text(ans.get("new_pose_key"), 40)
        if not pid and key and key in new_specs:
            if key in created:
                pid = created[key]
            else:
                spec = new_specs[key]
                similar, sim = E.similar_pose(library, spec["description"], spec.get("tags"), spec.get("number_shown"))
                if similar is not None:
                    pid = int(similar["id"])
                    reused_similar[key] = pid
                    notes.append(f"новая поза «{P._text(spec.get('title') or spec['description'], 50)}» ≈ готовой "
                                 f"#{pid} «{similar['title']}» (сходство {sim}) — беру готовую")
                elif len(created) >= MAX_NEW_POSES:
                    notes.append(f"бит {beat['i']}: больше {MAX_NEW_POSES} новых поз не рисую — подобрал по тегам")
                else:
                    pose = E.save_pose(store, int(mascot["id"]), description=spec["description"],
                                       title=spec.get("title") or "", tags=spec.get("tags") or [],
                                       emotion=spec.get("emotion") or "", number_shown=spec.get("number_shown"),
                                       origin="generated")
                    library.append(pose)
                    by_id[int(pose["id"])] = pose
                    pid = int(pose["id"])
                created[key] = pid
        if not pid:
            ranked = [p for p, _s in E.candidates([p for p in library if p["ready"]] or library, beat["text"], 4)]
            pid = int(ranked[0]["id"]) if ranked else 0
        if not pid:
            raise E.ExplError(f"бит {beat['i']}: нет позы — библиотека пуста")
        beat["pose_id"] = pid
        chip = P._text(ans.get("chip"), 10)
        beat["chip"] = {"text": chip, "word": P._text(ans.get("chip_word"), 40)} if chip else None
        beat["emphasis"] = [P._text(w, 40) for w in ans.get("emphasis") or [] if P._text(w)][:2]
    return notes


def _unrepeat(beats: list[dict], poses: list[dict]) -> None:
    """Две одинаковые позы подряд: вторую — на следующего кандидата; не нашлось — наезд (punch)."""
    ready = [p for p in poses]
    for n in range(1, len(beats)):
        if beats[n]["pose_id"] != beats[n - 1]["pose_id"]:
            continue
        nxt = beats[n + 1]["pose_id"] if n + 1 < len(beats) else None
        alt = next((p for p, s in E.candidates(ready, beats[n]["text"], 6)
                    if s > 0 and p["id"] not in (beats[n - 1]["pose_id"], nxt) and not p.get("number_shown")), None)
        if alt is not None:
            beats[n]["pose_id"] = int(alt["id"])
        else:
            beats[n]["variant"] = "punch"


def plan_beats(store: Any, video_id: int, client: Any = None) -> dict:
    """Биты ролика с позами и оверлеями (Gemini; без него — по тегам)."""
    video = E.get_video(store, video_id)
    if not E.narration_text(video):
        raise E.ExplError("у ролика нет сценария")
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    beats = split_beats(video["script"])
    poses = E.list_poses(store, int(mascot["id"]))
    notes: list[str] = []
    client = client if client is not None else gemini_client()
    source = "tags"
    if client is not None:
        try:
            data = _ask(client, PLAN_RULES, plan_context(store, video, beats, poses), plan_schema(), 0.4)
            notes += _apply_gemini_plan(store, mascot, beats, poses, data)
            source = "gemini"
        except Exception as exc:  # noqa: BLE001 - Gemini часто заблокирован: по тегам
            _log.warning("explainer %s: Gemini plan failed: %s", video_id, exc)
            notes.append(f"Gemini не ответил ({type(exc).__name__}: {str(exc)[:120]})")
            notes += _assign_fallback(beats, poses)
    else:
        notes += _assign_fallback(beats, poses)
    poses = E.list_poses(store, int(mascot["id"]))
    _unrepeat(beats, poses)
    by_id = {int(p["id"]): p for p in poses}
    for beat in beats:
        pose = by_id.get(int(beat["pose_id"]))
        beat["pose_title"] = pose["title"] if pose else ""
        beat["pose_new"] = bool(pose and not pose["ready"])
    new = sorted({b["pose_id"] for b in beats if b["pose_new"]})
    E.set_status(store, video_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "video_id": int(video_id), "beats": len(beats), "source": source,
            "reused": len({b["pose_id"] for b in beats}) - len(new), "new_poses": new, "notes": notes}


def update_beats(store: Any, video_id: int, changes: list[dict]) -> dict:
    """Агент/владелец правит биты до сборки: ``[{i, pose_id? | new_pose {description, title, tags,
    number_shown}, chip?, chip_word?, emphasis?, badge?}]`` → ролик снова «планируется» (новая оценка)."""
    video = E.get_video(store, video_id)
    if video["status"] in ("assembling",):
        raise E.ExplError("ролик собирается — дождись сборки")
    beats = video.get("beats") or []
    if not beats:
        if not E.narration_text(video):
            raise E.ExplError("сначала сценарий (explainer_set_script)")
        beats = split_beats(video["script"])
        for b in beats:
            b["pose_id"] = 0
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    library = E.list_poses(store, int(mascot["id"]))
    by_i = {int(b["i"]): b for b in beats}
    problems = []
    for ch in changes or []:
        if not isinstance(ch, dict) or ch.get("i") is None or int(ch["i"]) not in by_i:
            problems.append(f"нет бита {ch.get('i') if isinstance(ch, dict) else ch!r} (0–{len(beats) - 1})")
            continue
        beat = by_i[int(ch["i"])]
        if ch.get("pose_id"):
            pose = E.get_pose(store, int(ch["pose_id"]))
            if int(pose["mascot_id"]) != int(mascot["id"]):
                problems.append(f"бит {beat['i']}: поза {pose['id']} другого маскота")
                continue
            beat["pose_id"] = int(pose["id"])
        elif isinstance(ch.get("new_pose"), dict):
            spec = ch["new_pose"]
            similar, _sim = E.similar_pose(library, str(spec.get("description") or ""), spec.get("tags"),
                                           spec.get("number_shown"))
            pose = similar or E.save_pose(store, int(mascot["id"]), description=spec.get("description"),
                                          title=spec.get("title") or "", tags=spec.get("tags") or [],
                                          emotion=spec.get("emotion") or "", number_shown=spec.get("number_shown"),
                                          origin="agent")
            if similar is None:
                library.append(pose)
            beat["pose_id"] = int(pose["id"])
        if "chip" in ch:
            text = P._text(ch.get("chip"), 10)
            beat["chip"] = {"text": text, "word": P._text(ch.get("chip_word"), 40)} if text else None
        if "emphasis" in ch:
            beat["emphasis"] = [P._text(w, 40) for w in ch.get("emphasis") or [] if P._text(w)][:3]
        if "badge" in ch:
            beat["badge"] = P._text(ch.get("badge"), 6) or None
    if problems:
        raise E.ExplError("биты не приняты: " + "; ".join(problems))
    if any(not b.get("pose_id") for b in beats):
        raise E.ExplError("у каждого бита должна быть поза (pose_id или new_pose)")
    poses = {int(p["id"]): p for p in E.list_poses(store, int(mascot["id"]))}
    for beat in beats:
        pose = poses.get(int(beat["pose_id"]))
        beat["pose_title"] = pose["title"] if pose else ""
        beat["pose_new"] = bool(pose and not pose["ready"])
    E.set_status(store, video_id, "planned", None, beats=beats, error="", clip_id=None, timeline={})
    return E.get_video(store, video_id)


# --------------------------------------------------------------------------- #
# 3–4. Деньги и генерация
# --------------------------------------------------------------------------- #


def estimate(store: Any, video_id: int) -> dict:
    """Что ещё нужно ролику и сколько стоит (бесплатно, ничего не запускает)."""
    video = E.get_video(store, video_id)
    est = G.estimate(store, int(video["project_id"]), E.STAGE, None, [E.video_group(video_id)])
    poses = [i for i in est["items"] if i["ref_kind"] == E.POSE_KIND]
    tts = [i for i in est["items"] if i["kind"] == "tts"]
    reused = len({b["pose_id"] for b in video.get("beats") or []}) - len(poses)
    return {"video_id": int(video_id), "total_rub": est["total_rub"], "paid": est["paid"],
            "new_poses": len(poses), "poses_rub": round(sum(i["estimate_rub"] for i in poses), 2),
            "narration_rub": round(sum(i["estimate_rub"] for i in tts), 2), "reused_poses": max(0, reused),
            "narration_ready": not tts, "items": [{k: i.get(k) for k in ("label", "kind", "provider", "model",
                                                                           "estimate_rub", "price_note")}
                                                  for i in est["items"]],
            "problems": est["problems"], "budget": est["budget"], "note": est["note"]}


def request_generation(store: Any, video_id: int, requested_by: str = "explainer") -> dict:
    """Бесплатное (Gemini TTS, сухой прогон) — сразу; платное — одна карточка владельцу."""
    video = E.get_video(store, video_id)
    pid = int(video["project_id"])
    group = E.video_group(video_id)
    narr = G.estimate(store, pid, E.STAGE, None, [f"{group}:{E.NARRATION_KIND}"])
    if narr["items"] and not narr["paid"]:
        G.request_stage(store, pid, E.STAGE, None, [f"{group}:{E.NARRATION_KIND}"], requested_by=requested_by)
    est = estimate(store, video_id)
    if not est["items"]:
        E.set_status(store, video_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
        return {"status": "ready", "estimate": est}
    note = f"тема: {P._text(video['topic'], 200)}"
    try:
        req = G.request_stage(store, pid, E.STAGE, None, [group], requested_by=requested_by, note=note)
    except G.BudgetError as exc:
        E.set_status(store, video_id, "failed", ("planned",), error=str(exc)[:600])
        return {"status": "failed", "error": str(exc), "estimate": est}
    if req["status"] == "awaiting_approval":
        E.set_status(store, video_id, "awaiting_approval", ("planned",), approval_id=req["approval_id"],
                     estimate_rub=est["total_rub"])
        return {"status": "awaiting_approval", "approval_id": req["approval_id"], "estimate": est,
                "note": req["note"]}
    E.set_status(store, video_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
    return {"status": "generating", "estimate": est, "run": req.get("run")}


def _target_jobs(store: Any, project_id: int, target_type: str, target_id: int, kind: str) -> dict | None:
    row = store.db.query_one("SELECT * FROM aiv_gen_jobs WHERE project_id = ? AND target_type = ? AND target_id = ? "
                             "AND ref_kind = ? ORDER BY id DESC LIMIT 1",
                             (int(project_id), target_type, int(target_id), kind))
    return dict(row) if row else None


def readiness(store: Any, video_id: int) -> dict:
    """Готово ли всё к сборке: озвучка и картинка у каждой позы битов; что упало."""
    video = E.get_video(store, video_id)
    pid = int(video["project_id"])
    missing, failed, active, held = [], [], [], []
    checks = []
    if E.narration_ref(store, video) is None:
        checks.append(("expl_video", int(video_id), E.NARRATION_KIND, "озвучка"))
    for pose_id in sorted(set(E.beat_pose_ids(video))):
        pose = E.get_pose(store, pose_id)
        if not pose["ready"]:
            checks.append(("pose", pose_id, E.POSE_KIND, f"поза «{pose['title']}»"))
    for target_type, target_id, kind, label in checks:
        job = _target_jobs(store, pid, target_type, target_id, kind)
        missing.append(label)
        if job is None:
            continue
        if job["status"] in ("failed", "blocked_policy", "uncertain", "cancelled"):
            failed.append(f"{label}: {job['status']} — {(job.get('error') or '')[:160]}")
        elif job["status"] == "held":
            # Одобрено, но держится (AIVIDEO_ENABLED=0, потолок): включат — «Продолжить» у карточки.
            held.append(f"{label}: {(job.get('error') or 'держится')[:160]}")
        elif job["status"] in ("approved", "submitting", "polling", "recovering", "awaiting"):
            active.append(label)
    return {"ready": not missing, "missing": missing, "failed": failed, "active": active, "held": held}


def check_generation(store: Any, video_id: int) -> str:
    """awaiting_approval/generating → дальше. «wait» — ждём владельца или запуски."""
    video = E.get_video(store, video_id)
    if video["status"] == "awaiting_approval" and video.get("approval_id"):
        card = A.get_approval(store, int(video["approval_id"]))
        if card["status"] == "pending":
            return "wait"
        if card["status"] != "approved":
            E.set_status(store, video_id, "cancelled", ("awaiting_approval",),
                         error=f"карточка #{card['id']}: {card['status']}"
                               + (f" — {card.get('decision_comment')}" if card.get("decision_comment") else ""))
            return "cancelled"
        E.set_status(store, video_id, "generating", ("awaiting_approval",))
    state = readiness(store, video_id)
    if state["ready"]:
        E.set_status(store, video_id, "assembling", ("generating", "awaiting_approval"))
        return "ready"
    if state["active"]:
        return "wait"
    if state["held"] and not state["failed"]:
        E.update_video(store, video_id, error="ждёт запуска: " + "; ".join(state["held"][:2])
                       + " — включите генерацию и нажмите «Продолжить» у карточки")
        return "wait"
    if state["failed"]:
        E.set_status(store, video_id, "failed", ("generating", "awaiting_approval"),
                     error="генерация не удалась: " + "; ".join(state["failed"])[:600])
        return "failed"
    # Ничего не идёт и не упало, а чего-то нет (поза удалена/версия устарела) — запросить заново.
    E.set_status(store, video_id, "planned", ("generating", "awaiting_approval"))
    return "replan"


# --------------------------------------------------------------------------- #
# 5. Слова, время битов, сборка
# --------------------------------------------------------------------------- #


def transcribe_words(path: Path) -> list[dict]:
    """Слова озвучки с временем (Whisper large-v3-turbo, русский). Тесты подменяют."""
    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    result = WhisperSubtitleProvider().transcribe(Path(path), {"language": "ru"}, QUALITY_WHISPER_MODEL)
    return [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
            for w in result.words if str(w.word or "").strip()]


def proportional_words(text: str, duration: float) -> list[dict]:
    """Слова текста равномерно по длине (сухой прогон: вместо речи тон)."""
    words = text.split()
    if not words:
        return []
    weights = [len(re.sub(r"\W", "", w)) + 1.0 for w in words]
    start, end = 0.1, max(0.5, duration - 0.1)
    scale = (end - start) / sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = wt * scale
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def _norm(word: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def align(beats: list[dict], words: list[dict]) -> list[int]:
    """Индекс первого слова озвучки каждого бита (difflib по словам текста и распознанным)."""
    script_tokens: list[str] = []
    owner: list[int] = []
    for n, beat in enumerate(beats):
        for w in beat["text"].split():
            script_tokens.append(_norm(w))
            owner.append(n)
    spoken = [_norm(w["word"]) for w in words]
    mapping = [0] * len(script_tokens)
    sm = difflib.SequenceMatcher(None, script_tokens, spoken, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(i1, i2):
            if tag == "equal":
                mapping[k] = j1 + (k - i1)
            elif tag == "replace":
                span = max(1, i2 - i1)
                mapping[k] = min(j2 - 1, j1 + int((k - i1) * (j2 - j1) / span)) if j2 > j1 else j1
            else:  # delete: слова нет в распознанном — к следующему
                mapping[k] = j1
    firsts = []
    for n in range(len(beats)):
        ks = [k for k, o in enumerate(owner) if o == n]
        firsts.append(min(len(words) - 1, mapping[ks[0]]) if ks and words else 0)
    for n in range(1, len(firsts)):  # монотонно
        firsts[n] = max(firsts[n], firsts[n - 1])
    return firsts


def place(beats: list[dict], words: list[dict], total: float) -> list[dict]:
    """Биты по времени: старт — начало первого слова; бит длиннее ``REAL_SPLIT_SEC`` делится тем же
    кадром с наездом; слипшиеся (< 0.35 с) — в соседний."""
    if not words:
        raise E.ExplError("нет слов озвучки")
    firsts = align(beats, words)
    placed: list[dict] = []
    for n, beat in enumerate(beats):
        start = 0.0 if n == 0 else round(float(words[firsts[n]]["start"]), 3)
        w0 = 0 if n == 0 else firsts[n]
        if placed and start - placed[-1]["start"] < 0.35:
            # Слово не нашлось или бит слишком короткий — сливаем с прошлым (поза прошлого).
            placed[-1]["text"] += " " + beat["text"]
            if beat.get("badge") and not placed[-1].get("badge"):
                placed[-1]["badge"] = beat["badge"]
            if beat.get("chip") and not placed[-1].get("chip"):
                placed[-1]["chip"] = beat["chip"]
            placed[-1]["emphasis"] = (placed[-1].get("emphasis") or []) + (beat.get("emphasis") or [])
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
            first = {**item, "end": cut, "w1": k - 1}
            second = {**item, "start": cut, "w0": k, "variant": "punch", "badge": None, "split_of": item["i"]}
            if item.get("chip") and _word_in(words, item["chip"].get("word"), item["w0"], k - 1) is None:
                first["chip"] = None
            else:
                second["chip"] = None
            out += [first, second]
        else:
            out.append(item)
    return out


def _word_in(words: list[dict], target: Any, w0: int, w1: int) -> int | None:
    """Индекс слова озвучки в [w0, w1]: то же слово, та же основа (4 буквы) или число («семь» → «7»)."""
    key = _norm(target)
    if not key:
        return None
    keys = [key] + ([_NUMBER_WORD[key]] if key in _NUMBER_WORD else [])
    for want in keys:
        for k in range(max(0, w0), min(len(words) - 1, w1) + 1):
            word = _norm(words[k]["word"])
            if word == want or (len(want) >= 4 and word.startswith(want[:4])):
                return k
    return None


_NUMBER_WORD = {"ноль": "0", "один": "1", "одну": "1", "два": "2", "две": "2", "три": "3", "четыре": "4",
                "пять": "5", "шесть": "6", "семь": "7", "восемь": "8", "девять": "9", "десять": "10",
                "двенадцать": "12", "двух": "2", "трех": "3"}


def emphasis_marks(placed: list[dict], words: list[dict]) -> dict[int, int]:
    """Индексы слов субтитров с эмфазой: ключевые слова битов + числа чипов."""
    marks: dict[int, int] = {}
    for item in placed:
        targets = list(item.get("emphasis") or [])
        if item.get("chip") and item["chip"].get("word"):
            targets.append(item["chip"]["word"])
        for target in targets:
            k = _word_in(words, target, item["w0"], item["w1"])
            if k is not None:
                marks[k] = 1
    return marks


def _sfx_ok(ref: str) -> bool:
    from app.motion_assets import resolve_pack_ref

    try:
        return resolve_pack_ref(ref) is not None
    except Exception:  # noqa: BLE001
        return False


def build_layers(store: Any, mascot: dict, placed: list[dict], words: list[dict], narration_asset: int,
                 total: float, notes: list[str]) -> list[dict]:
    """Слои как в пилоте 887 (позы, вжухи, плашки, чипы, голос, субтитры)."""
    poses = {int(p["id"]): p for p in E.list_poses(store, int(mascot["id"]))}
    sfx = {ref: _sfx_ok(ref) for ref in (*CUT_SFX, BADGE_SFX, CHIP_SFX)}
    if not all(sfx.values()):
        notes.append("пак freesound не установлен (tools/fetch_sfx.py) — без вжухов/«динь»/«поп»: синтетику не "
                     "подставляю")
    accent, chip_bg = mascot["accent_color"], mascot["chip_color"]
    layers: list[dict] = []
    for n, item in enumerate(placed):
        pose = poses.get(int(item["pose_id"]))
        if pose is None or not pose["ready"]:
            raise E.ExplError(f"бит {item['i']}: у позы {item['pose_id']} нет картинки")
        end = item["end"]
        layer = {"id": f"pose{n:02d}", "type": "image", "name": f"Поза: {pose['title']}"[:60],
                 "start": item["start"], "end": end, "x": 0.5, "y": 0.5,
                 "props": {"asset_id": str(pose["asset_id"]), "w": 1080},
                 "in": {"type": "pop" if n % 2 == 0 else "zoom", "duration": POP_SEC}}
        if item.get("variant") == "punch":
            layer["scale"] = PUNCH_SCALE
            layer["in"] = {"type": "zoom", "duration": POP_SEC}
        layers.append(layer)
        if n > 0:
            ref = CUT_SFX[n % 2 == 0]
            if sfx[ref]:
                layers.append({"id": f"wh{n:02d}", "type": "audio", "start": round(max(0.0, item["start"] - 0.05), 3),
                               "end": round(min(total, item["start"] + 0.6), 3),
                               "props": {"asset_id": ref, "mode": "track", "volume": WHOOSH_VOLUME}})
    badge_n = 0
    chip_n = 0
    badge_spans: list[tuple[float, float]] = []
    for item in placed:
        if not item.get("badge"):
            continue
        s, e = item["start"], round(min(item["end"], item["start"] + BADGE_MAX_SEC), 3)
        layers.append({"id": f"badge{badge_n}", "type": "text", "start": s, "end": e, "x": 0.5, "y": 0.2,
                       "props": {"text": item["badge"], "font": "Montserrat", "weight": 900, "size": 170,
                                 "color": accent, "stroke": {"color": mascot["outline_color"], "width": 10},
                                 "align": "center"},
                       "in": {"type": "pop", "duration": 0.2}})
        if sfx[BADGE_SFX]:
            layers.append({"id": f"ding{badge_n}", "type": "audio", "start": s, "end": round(min(total, s + 1.0), 3),
                           "props": {"asset_id": BADGE_SFX, "mode": "track", "volume": DING_VOLUME}})
        badge_spans.append((s, e))
        badge_n += 1
    for item in placed:
        chip = item.get("chip")
        if not chip or not chip.get("text"):
            continue
        k = _word_in(words, chip.get("word"), item["w0"], item["w1"])
        s = max(item["start"], round(float(words[k]["start"]) - 0.15, 3)) if k is not None else item["start"]
        e = round(min(item["end"], s + CHIP_SEC), 3)
        if e - s < 0.5:
            s, e = item["start"], round(min(item["end"], item["start"] + CHIP_SEC), 3)
        y = 0.31 if any(a < e and s < b for a, b in badge_spans) else 0.22
        layers.append({"id": f"chip{chip_n}", "type": "text", "start": s, "end": e, "x": 0.5, "y": y,
                       "props": {"text": chip["text"], "font": "Montserrat", "weight": 900, "size": 120,
                                 "color": "#ffffff", "align": "center",
                                 "bg": {"color": chip_bg, "opacity": 1, "radius": 24, "padX": 28, "padY": 10}},
                       "in": {"type": "pop", "duration": POP_SEC}})
        if sfx[CHIP_SFX]:
            layers.append({"id": f"pop{chip_n}", "type": "audio", "start": s, "end": round(min(total, s + 0.6), 3),
                           "props": {"asset_id": CHIP_SFX, "mode": "track", "volume": POP_VOLUME}})
        chip_n += 1
    layers.append({"id": "narr", "type": "audio", "name": "Озвучка", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(narration_asset), "mode": "track", "volume": 1.0}})
    layers.append({"id": "caps", "type": "captions", "name": "Субтитры", "start": 0.0, "end": round(total, 3),
                   "x": 0.5, "y": 0.84,
                   "props": {"style": mascot.get("captions_style") or "hormozi", "size": 92, "activeColor": accent,
                             "uppercase": True, "maxWords": 3, "maxLines": 2, "emphColor": accent}})
    return layers


def _speech_spans(words: list[dict]) -> list[dict]:
    spans: list[list[float]] = []
    for w in words:
        a, b = max(0.0, w["start"] - 0.1), w["end"] + 0.15
        if spans and a - spans[-1][1] < 0.6:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [{"start": round(a, 3), "end": round(b, 3), "gain_db": MUSIC_DUCK_DB} for a, b in spans]


def _music_track(store: Any, mascot: dict, video: dict) -> int | None:
    opt = (video.get("options") or {}).get("music_track_id")
    for value in (opt, mascot.get("music_track_id"), V._style_row(store, int(video["project_id"])).get("music_track_id")):
        if value in (None, "", 0):
            continue
        try:
            store.get_audio_track(int(value))
            return int(value)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _tool(ctx: Any, name: str, args: dict, notes: list[str] | None = None) -> dict:
    """Инструмент монтажа (те же проверки, что у агента). Пропущенные слои пачки — в заметки."""
    from app import agent_tools as T

    result, ok = T.run_tool(ctx, name, args)
    if not ok:
        detail = result.get("problems") if isinstance(result, dict) else None
        raise E.ExplError(f"{name}: {(result or {}).get('error') if isinstance(result, dict) else result}"
                          + (f" — {detail}" if detail else ""))
    if notes is not None and isinstance(result, dict) and result.get("skipped"):
        notes += [f"{name}: пропущено — {s}"[:240] for s in result["skipped"]]
    return result


def assemble(store: Any, video_id: int) -> dict:
    """Озвучка → слова → биты по времени → новый холст-клип как пилот 887."""
    from app import agent_tools as T
    from app.aivideo_gen import _asset_path
    from app.canvas import base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration

    video = E.get_video(store, video_id)
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    ref = E.narration_ref(store, video)
    if ref is None:
        raise E.ExplError("нет озвучки ролика — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = list(video.get("notes") or [])
    voice = E.mascot_voice(store, mascot)
    words: list[dict] = []
    if voice["provider"] != "none":
        try:
            words = transcribe_words(path)
        except Exception as exc:  # noqa: BLE001 - Whisper выключен/не встал: по длине текста
            notes.append(f"Whisper не сработал ({type(exc).__name__}: {str(exc)[:120]}) — слова по длине текста")
    if not words:
        words = proportional_words(E.narration_text(video), audio_sec)
    total = round(max(words[-1]["end"] + TAIL_SEC, audio_sec + 0.4), 2)
    if total > base_seconds():
        raise E.ExplError(f"ролик {total:.1f} с длиннее холста {base_seconds():.0f} с")
    placed = place(video["beats"], words, total)
    marks = emphasis_marks(placed, words)
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": [mascot["bg_color"], mascot["bg_color"]]},
                              (video.get("title") or video["topic"])[:100],
                              f"Объяснялка ({mascot['name']}): {video['topic']}"[:500])
    clip_id = int(clip["id"])
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id,
                        on_first_change=lambda: snapshot(store, clip_id, "объяснялка: сборка"))
    layers = build_layers(store, mascot, placed, words, int(ref["asset_id"]), total, notes)
    for i in range(0, len(layers), LAYER_BATCH):
        _tool(ctx, "add_layers", {"layers": layers[i:i + LAYER_BATCH]}, notes)
    _tool(ctx, "set_subtitle_words", {"words": [{**w, **({"emph": marks[k]} if k in marks else {})}
                                                for k, w in enumerate(words)]})
    track = _music_track(store, mascot, video)
    if track:
        _tool(ctx, "set_music", {"on": True, "track_id": track})
        _tool(ctx, "set_music_duck", {"duck": _speech_spans(words)})
    else:
        notes.append("музыка не выбрана (маскот без трека) — set_music по list_music")
    E.mark_used(store, [int(p["pose_id"]) for p in placed])
    fresh = store.get_clip_plan(clip_id)
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2),
                "editor_url": f"/projects/{fresh['source_id']}/candidates?clip={clip_id}",
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "pose_id", "pose_title", "badge", "chip",
                                                  "variant")} for p in placed],
                "emphasis_words": len(marks), "layers": len(layers), "music_track_id": track}
    E.set_status(store, video_id, "done", ("assembling",), clip_id=clip_id, words=words, timeline=timeline,
                 notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "layers": len(layers),
           "editor_url": timeline["editor_url"], "notes": notes}
    options = video.get("options") or {}
    if options.get("render"):
        out["publish"] = finish_publish(store, video_id, clip_id, cover=options.get("cover", True))
    return out


def finish_publish(store: Any, video_id: int, clip_id: int, cover: bool = True) -> dict:
    """По желанию: рендер, обложки, заголовок/описание (модули обложек и метаданных; Gemini может не
    ответить — тогда без текста, ролик всё равно готов)."""
    from app.render import ClipRenderService

    out: dict[str, Any] = {}
    notes = list(E.get_video(store, video_id).get("notes") or [])
    try:
        clip = ClipRenderService(store).render_clip_plan(clip_id)
        out["render"] = {"clip": clip.get("id"), "status": clip.get("status")}
        if clip.get("status") == "succeeded":
            if cover:
                try:
                    from app.cover_variants import generate_covers

                    covers = generate_covers(store, int(clip["id"]), count=3)
                    out["covers"] = len(covers.get("covers") or [])
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
    E.update_video(store, video_id, notes=notes)
    return out


# --------------------------------------------------------------------------- #
# Ведущий: advance / работник
# --------------------------------------------------------------------------- #

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(video_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(video_id), threading.Lock())


def advance(store: Any, video_id: int, client: Any = None, max_steps: int = 8) -> dict:
    """Двигать ролик по шагам, пока не упрётся в ожидание (карточка, запуски) или конец.
    Ошибка шага — ``failed`` с текстом (продолжить — ``resume``/``explainer_continue``)."""
    lock = _lock(video_id)
    if not lock.acquire(blocking=False):
        return {**E.get_video(store, video_id, full=False), "busy": True}
    try:
        for _ in range(max_steps):
            video = E.get_video(store, video_id, full=False)
            status = video["status"]
            try:
                if status == "new":
                    write_script(store, video_id, client=client)
                elif status == "scripted":
                    plan_beats(store, video_id, client=client)
                elif status == "planned":
                    if request_generation(store, video_id)["status"] in ("awaiting_approval", "failed"):
                        break
                elif status in ("awaiting_approval", "generating"):
                    if check_generation(store, video_id) in ("wait", "failed", "cancelled"):
                        break
                elif status == "assembling":
                    assemble(store, video_id)
                else:
                    break
            except Exception as exc:  # noqa: BLE001 - шаг упал: ролик ждёт правки
                _log.warning("explainer %s step %s failed: %s", video_id, status, exc)
                E.set_status(store, video_id, "failed", None, error=f"{STEP_LABELS.get(status, status)}: "
                                                                   f"{str(exc)[:500]}")
                break
    finally:
        lock.release()
    return E.get_video(store, video_id)


STEP_LABELS = {"new": "сценарий", "scripted": "подбор поз", "planned": "оценка", "awaiting_approval": "одобрение",
               "generating": "генерация", "assembling": "сборка"}


def resume(store: Any, video_id: int, client: Any = None) -> dict:
    """Продолжить упавший/отменённый ролик с того шага, где есть данные."""
    video = E.get_video(store, video_id)
    if video["status"] in ("failed", "cancelled"):
        if not E.narration_text(video):
            status = "new"
        elif not video.get("beats"):
            status = "scripted"
        else:
            status = "planned"
        E.set_status(store, video_id, status, ("failed", "cancelled"), error="")
    return advance(store, video_id, client=client)


def cancel(store: Any, video_id: int) -> dict:
    video = E.get_video(store, video_id, full=False)
    if video.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'ролик отменён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(video["approval_id"]),))
        try:
            card = A.get_approval(store, int(video["approval_id"]))
            if card["status"] == "pending":
                A._update(store, card["id"], status="expired", decision_comment="ролик отменён")
        except KeyError:
            pass
    E.set_status(store, video_id, "cancelled", None, error="отменено")
    return E.get_video(store, video_id)


def create(store: Any, mascot_id: int, topic: str, duration: Any = 45, tone: str = "", script: dict | None = None,
           options: dict | None = None, requested_by: str = "agent", run: bool = False, client: Any = None) -> dict:
    """Новый ролик. ``script`` — свой текст (без Gemini). ``run`` — сразу вести по шагам (иначе работник)."""
    video = E.create_video(store, mascot_id, topic, duration, tone, options, requested_by)
    if script:
        set_script(store, int(video["id"]), script, source=requested_by)
    if run:
        return advance(store, int(video["id"]), client=client)
    kick()
    return E.get_video(store, int(video["id"]))


_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class ExplainerWorker:
    """Фон: ведёт ролики по шагам (сценарий, позы, карточка, ожидание, сборка). Платные запуски
    выполняет работник ИИ-видео после «Разрешить». ``EXPLAINER_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 5

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        global _wake
        if os.getenv("EXPLAINER_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="explainer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        marks = ", ".join("?" for _ in E.ACTIVE_STATUSES)
        rows = self.store.db.query_all(f"SELECT id FROM expl_videos WHERE status IN ({marks}) ORDER BY id",
                                       E.ACTIVE_STATUSES)
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
                _log.warning("explainer worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()
