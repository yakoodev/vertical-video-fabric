"""Publishing metadata for a clip: title, description and hashtags.

Text-only Gemini call fed with what the clip actually contains — the moment's
title/description/category and the Whisper transcript lines inside the clip's
segments — steered by the default «publishing» prompt preset (editable in
Settings → Промпты). Cheap: a few hundred input tokens on a flash model.

Канал (проект-серия, app/projects.py): лор (люди, шутки, табу) и активные правила владельца
идут в тот же запрос — названия держатся правил канала. Ответ, кроме общего заголовка и
описания, даёт варианты под площадки (``platforms``: youtube / tiktok) и тексты для обложки
(``cover_hooks``, app/cover_variants.py).

После рендера (``auto_meta_after_render``, зовёт ``ClipRenderService.render_clip_plan``)
метаданные пишутся сами, если у клипа их ещё нет: включено настройкой ``auto_publish_meta``
(по умолчанию да) и есть ключ Gemini. Новый рендер того же клипа наследует прежние
метаданные (и ручной заголовок владельца) без нового запроса. Ошибка не ломает рендер.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

_log = logging.getLogger(__name__)

MAX_TITLE = 95  # YouTube caps titles at 100 chars
MAX_TRANSCRIPT_CHARS = 4000
# Площадки, под которые модель пишет свой вариант заголовка и описания.
PLATFORM_KEYS = ("youtube", "tiktok")
# app_settings: писать ли заголовок/описание сами после рендера (нет записи — да).
AUTO_META_KEY = "auto_publish_meta"

BASE_RULES = """\
Ты пишешь метаданные для публикации вертикального клипа (YouTube Shorts, TikTok, Instagram Reels).
Язык — русский (как в клипе). Верни ТОЛЬКО JSON по схеме.
- title: до 90 символов, конкретно про то, что происходит в клипе, цепляет, без капса и без вранья о содержании.
- description: 1–3 коротких предложения, раскрывают момент и зовут досмотреть; без хэштегов внутри.
- hashtags: 5–10 штук, каждый начинается с #, без пробелов, строчными; сначала 2–4 по теме клипа, затем общие (#shorts, #стрим, #нарезка и т.п. по смыслу).
- youtube: вариант для YouTube {title, description}: title до 90 символов со словами, по которым ищут; description 1–3 предложения.
- tiktok: вариант для TikTok {title, description}: title — короткая разговорная подпись до 80 символов; description — одна фраза или пусто.
- cover_hooks: 3 разных текста для обложки, 2–5 слов каждый: крупно читается, эмоция или интрига момента, без вранья и без хэштегов.
- Без эмодзи. Не упоминай другие сервисы нарезки и чужие бренды.
"""


# Хэштеги вертикальных лент — у обычного горизонтального видео YouTube они лишние.
VERTICAL_TAGS = {"#shorts", "#short", "#reels", "#reel", "#tiktok", "#шортс", "#шортсы"}


def rules_for_format(fmt: str) -> str:
    """BASE_RULES под формат клипа (app/formats.py). 9:16 — прежний текст; 16:9 — обычное видео
    YouTube (не Shorts: другой заголовок/описание, без #shorts); 1:1 и 4:5 — пост в ленту."""
    header = "Ты пишешь метаданные для публикации вертикального клипа (YouTube Shorts, TikTok, Instagram Reels)."
    if fmt == "16:9":
        return BASE_RULES.replace(header, (
            "Ты пишешь метаданные для публикации ГОРИЗОНТАЛЬНОГО видео 16:9 на YouTube — это обычное видео, "
            "не Shorts/TikTok/Reels (если указания пресета ниже говорят про Shorts — формат клипа важнее)."
        )).replace(
            "- description: 1–3 коротких предложения, раскрывают момент и зовут досмотреть; без хэштегов внутри.",
            "- description: 2–4 предложения, как у ролика YouTube: что произошло, контекст, зачем смотреть; "
            "без хэштегов внутри.",
        ).replace(
            "затем общие (#shorts, #стрим, #нарезка и т.п. по смыслу).",
            "затем общие (#стрим, #нарезка и т.п. по смыслу). НЕ ставь #shorts, #reels, #tiktok — это не шортс.",
        )
    if fmt in ("1:1", "4:5"):
        shape = "квадратного клипа 1:1" if fmt == "1:1" else "клипа 4:5"
        return BASE_RULES.replace(header, (
            f"Ты пишешь метаданные для публикации {shape} (пост в ленту Instagram, YouTube Shorts)."
        ))
    return BASE_RULES


def _schema() -> dict[str, Any]:
    variant = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "propertyOrdering": ["title", "description"],
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "youtube": variant,
            "tiktok": variant,
            "cover_hooks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "description", "hashtags"],
        "propertyOrdering": ["title", "description", "hashtags", "youtube", "tiktok", "cover_hooks"],
    }


def clip_context(store: Any, clip: dict) -> dict[str, Any]:
    """Collect what the model should know about the clip."""
    plan: dict = {}
    if clip.get("clip_plan_id"):
        try:
            plan = store.get_clip_plan(int(clip["clip_plan_id"]))
        except KeyError:
            plan = {}
    segments = plan.get("segments") or []
    if not segments and clip.get("segment_id"):
        segments = [
            s for s in (store.get_source(int(clip["source_id"]), include_related=True).get("segments") or [])
            if s.get("id") == clip["segment_id"]
        ]
    ranges = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    lines: list[str] = []
    if ranges:
        try:
            cues = store.get_source_transcript(int(clip["source_id"]))
        except KeyError:
            cues = []
        for cue in cues:
            try:
                a, b = float(cue["start"]), float(cue["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(a < e and b > s for s, e in ranges):
                text = str(cue.get("text") or "").strip()
                if text:
                    lines.append(text)
    transcript = " ".join(lines)[:MAX_TRANSCRIPT_CHARS]
    return {
        "moment_title": plan.get("title") or clip.get("title") or "",
        "moment_description": plan.get("description") or clip.get("description") or "",
        "category": plan.get("category") or "",
        "segment_notes": [s.get("title") or "" for s in segments if s.get("title")][:6],
        "duration_sec": round(float(clip.get("duration_sec") or 0), 1),
        "transcript": transcript,
    }


def project_context(store: Any, clip: dict) -> dict[str, Any]:
    """Канал клипа для запроса: лор (кто есть кто, шутки, табу) и активные правила владельца.
    Нет проекта или хранилище без проектов — пусто (запрос как раньше)."""
    try:
        from app import projects as P

        project_id = None
        if clip.get("clip_plan_id"):
            project_id = P.project_of_plan(store, int(clip["clip_plan_id"]))
        if project_id is None and clip.get("source_id"):
            project_id = P.project_of_source(store, int(clip["source_id"]))
        if project_id is None:
            return {}
        project = P.get_project(store, int(project_id), include_sources=False)
        brief = P.get_brief(store, int(project_id))
        rules = P.list_rules(store, project_id=int(project_id))
        from app.project_knowledge import list_facts

        facts = list_facts(store, int(project_id))
    except Exception:  # noqa: BLE001 - без проекта метаданные всё равно пишутся
        return {}
    out: dict[str, Any] = {
        "name": project.get("name") or "",
        "about": str(project.get("description") or "")[:400],
        "summary": str(brief.get("summary") or "")[:600],
        "people": [{"name": p.get("name"), "aliases": p.get("aliases") or []} for p in brief.get("people") or []][:12],
        "jokes": [j.get("text") for j in brief.get("jokes") or [] if j.get("text")][:8],
        "taboo": [t.get("topic") for t in brief.get("taboo") or [] if t.get("topic")][:10],
        "rules": [{"kind": r.get("kind"), "text": r.get("text")} for r in rules if r.get("text")][:20],
        "facts": [f["text"] for f in facts][:20],
    }
    return {k: v for k, v in out.items() if v}


# Хэштег — только кириллица, латиница, цифры и «_». ``\w`` пропускал иероглифы: QA 02.11 (клип 892) —
# в авто-описании «#таракан皮».
_TAG_JUNK = re.compile(r"[^0-9a-zа-яё_]")
_INLINE_TAG = re.compile(r"#[^\s#]+")


def clean_tag(tag: Any) -> str:
    """Один хэштег без «#»: строчными, без пробелов и символов вне кириллицы/латиницы/цифр/_ ("" — мусор)."""
    t = re.sub(r"\s+", "", str(tag or "")).lstrip("#").lower()
    return _TAG_JUNK.sub("", t)


def _clean_hashtags(raw: Any) -> list[str]:
    out: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        t = clean_tag(tag)
        if t and f"#{t}" not in out:
            out.append(f"#{t}")
    return out[:10]


def clean_inline_hashtags(text: Any) -> str:
    """Хэштеги внутри текста описания — тем же правилом; пустой после чистки — убран."""

    def fix(match: re.Match) -> str:
        tag = clean_tag(match.group(0))
        return f"#{tag}" if tag else ""

    return _INLINE_TAG.sub(fix, str(text or "")).strip()


def strip_emoji(text: Any) -> str:
    """Без эмодзи и значков: только буквы, цифры, пробелы и обычная пунктуация."""
    out = []
    for ch in str(text or ""):
        cat = unicodedata.category(ch)
        if ch in "\n\t":
            out.append(" ")
        elif cat[0] in "LNP" or ch == " " or ch in "+%№$€₽":
            out.append(ch)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _json_body(text: str) -> dict:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("модель вернула не-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("модель вернула не объект")
    return data


def _variants(data: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in PLATFORM_KEYS:
        raw = data.get(key)
        if not isinstance(raw, dict):
            continue
        title = " ".join(strip_emoji(raw.get("title")).split())[:MAX_TITLE].strip()
        if title:
            out[key] = {"title": title,
                        "description": clean_inline_hashtags(strip_emoji(raw.get("description")))[:2000]}
    return out


def parse_metadata(text: str) -> dict[str, Any]:
    data = _json_body(text)
    title = " ".join(str(data.get("title") or "").split())[:MAX_TITLE].strip()
    description = clean_inline_hashtags(data.get("description"))
    if not title:
        raise RuntimeError("модель не дала заголовок")
    meta: dict[str, Any] = {"title": title, "description": description, "hashtags": _clean_hashtags(data.get("hashtags"))}
    platforms = _variants(data)
    if platforms:
        meta["platforms"] = platforms
    if isinstance(data.get("cover_hooks"), list):
        from app.cover_variants import clean_hook

        hooks = [h for h in (clean_hook(x) for x in data["cover_hooks"]) if h]
        if hooks:
            meta["cover_hooks"] = list(dict.fromkeys(hooks))[:6]
    return meta


def generate_publish_metadata(
    store: Any, clip_id: int, client: GeminiClient | None = None, model: str | None = None
) -> dict[str, Any]:
    from app.publish_format import clip_format

    clip = store.get_clip(clip_id)
    context = clip_context(store, clip)
    fmt = clip_format(store, clip) or "9:16"
    if fmt != "9:16":
        context["format"] = fmt
    preset = store.get_default_prompt_preset("publishing")
    user_rules = str((preset or {}).get("prompt") or "").strip()
    channel = project_context(store, clip)
    prompt = (
        rules_for_format(fmt)
        + (f"\nДополнительные указания (пресет «{preset.get('label')}»):\n{user_rules}\n" if user_rules else "")
        + (
            "\nКанал клипа — соблюдай его правила (dont — запрещено, do — обязательно, preference — по "
            "возможности), темы из taboo не упоминай, имена пиши как в лоре (JSON):\n"
            + json.dumps(channel, ensure_ascii=False) + "\n"
            if channel else ""
        )
        + "\nДанные клипа (JSON):\n"
        + json.dumps(context, ensure_ascii=False)
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "responseMimeType": "application/json",
            "responseSchema": _schema(),
        },
    }
    used_model = model or settings.gemini_text_model
    response = (client or GeminiClient()).generate_content(used_model, payload)
    meta = parse_metadata(_extract_response_text(response))
    meta["model"] = used_model
    meta["has_transcript"] = bool(context["transcript"])
    if fmt != "9:16":
        meta["format"] = fmt
    if fmt == "16:9":  # обычное видео YouTube: теги вертикальных лент модели не оставляем
        from app.publish_format import strip_shorts_tags

        meta["hashtags"] = [t for t in meta["hashtags"] if t not in VERTICAL_TAGS]
        meta["title"] = strip_shorts_tags(meta["title"]) or meta["title"]
        meta["description"] = strip_shorts_tags(meta["description"])
        for variant in (meta.get("platforms") or {}).values():
            variant["title"] = strip_shorts_tags(variant["title"]) or variant["title"]
            variant["description"] = strip_shorts_tags(variant["description"])
    if channel:
        meta["channel_rules"] = len(channel.get("rules") or [])
    return meta


def full_description(description: str, hashtags: list[str]) -> str:
    """Описание с хэштегами в конце — так оно хранится на клипе и уходит на площадку."""
    return "\n\n".join(x for x in (str(description or "").strip(), " ".join(hashtags or [])) if x)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def save_publish_meta(store: Any, clip_id: int, meta: dict, source: str = "ai") -> dict:
    """Записать метаданные на клип: заголовок, описание с хэштегами и полную запись
    (варианты площадок, тексты обложки, откуда взялось) в ``publish_meta``."""
    record = {
        "title": meta["title"],
        "description": meta.get("description") or "",
        "hashtags": list(meta.get("hashtags") or []),
        "platforms": dict(meta.get("platforms") or {}),
        "cover_hooks": list(meta.get("cover_hooks") or []),
        "model": meta.get("model") or "",
        "source": source,
        "generated_at": _now(),
    }
    store.update_clip(clip_id, title=record["title"],
                      description=full_description(record["description"], record["hashtags"]))
    store.set_clip_publish_meta(clip_id, record)
    return record


def platform_texts(store: Any, job: dict, platform: str) -> tuple[str, str]:
    """Заголовок и описание задания для площадки. Если задание несёт текст клипа как есть
    (владелец его не правил), а у клипа есть вариант этой площадки — берём вариант (хэштеги
    те же). Иначе — текст задания. Никогда не падает: публикация важнее варианта."""
    title, description = str(job.get("title") or ""), str(job.get("description") or "")
    try:
        if not job.get("clip_id"):
            return title, description
        clip = store.get_clip(int(job["clip_id"]))
        meta = clip.get("publish_meta") or {}
        variant = (meta.get("platforms") or {}).get(platform) or {}
        if not variant.get("title"):
            return title, description
        if title.strip() != str(clip.get("title") or "").strip():
            return title, description
        if description.strip() and description.strip() != str(clip.get("description") or "").strip():
            return title, description
        return variant["title"], full_description(variant.get("description") or meta.get("description") or "",
                                                  meta.get("hashtags") or [])
    except Exception:  # noqa: BLE001 - вариант — уточнение, не повод сорвать публикацию
        return title, description


def generate_cover_hooks(
    store: Any, clip_id: int, count: int = 3, client: GeminiClient | None = None, model: str | None = None
) -> list[str]:
    """Тексты для обложки (2–5 слов) отдельным коротким запросом — когда их нет в метаданных клипа."""
    from app.cover_variants import clean_hook

    clip = store.get_clip(clip_id)
    context = clip_context(store, clip)
    context["clip_title"] = clip.get("title") or ""
    channel = project_context(store, clip)
    prompt = (
        f"Придумай {max(1, int(count))} разных текста для обложки короткого ролика. Каждый 2–5 слов, по-русски, "
        "крупно читается с телефона: эмоция или интрига момента, без вранья о содержании, без эмодзи, без "
        "хэштегов, без названий площадок и чужих брендов. Верни ТОЛЬКО JSON {\"hooks\": [..]}.\n"
        + ("Правила канала (JSON): " + json.dumps(channel, ensure_ascii=False) + "\n" if channel else "")
        + "Данные клипа (JSON):\n" + json.dumps(context, ensure_ascii=False)
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "responseMimeType": "application/json",
            "responseSchema": {"type": "object", "properties": {"hooks": {"type": "array", "items": {"type": "string"}}},
                               "required": ["hooks"]},
        },
    }
    response = (client or GeminiClient(timeout=90)).generate_content(model or settings.gemini_text_model, payload)
    data = _json_body(_extract_response_text(response))
    hooks = [h for h in (clean_hook(x) for x in data.get("hooks") or []) if h]
    return list(dict.fromkeys(hooks))[: max(1, int(count))]


def auto_meta_enabled(store: Any) -> bool:
    """Писать ли метаданные сами после рендера: env AUTO_PUBLISH_META, ключ Gemini и
    настройка ``auto_publish_meta`` (нет записи — да)."""
    if not settings.auto_publish_meta or not settings.gemini_api_key:
        return False
    from app import modules

    if not modules.is_enabled("publish_meta", store):  # «Настройки → Модули»
        return False
    try:
        return bool(store.get_app_setting_value(AUTO_META_KEY, True))
    except Exception:  # noqa: BLE001 - нет настроек — по умолчанию
        return True


def auto_meta_after_render(store: Any, clip: dict, client: GeminiClient | None = None) -> dict:
    """Заголовок, описание, хэштеги, варианты площадок и тексты обложки для только что
    отрендеренного клипа — если их ещё нет. Прежний рендер того же клипа с метаданными (или
    с заголовком, который владелец поправил руками) — переносим без запроса. Никогда не
    бросает: ответ ``{status: generated|inherited|skipped|failed, ...}``."""
    clip_id = int(clip.get("id") or 0)
    try:
        if clip.get("status") != "succeeded" or not clip_id:
            return {"status": "skipped", "reason": "рендер не готов"}
        fresh = store.get_clip(clip_id)
        if fresh.get("publish_meta"):
            return {"status": "skipped", "reason": "уже есть"}
        # Перенос текста с прежнего рендера модели не требует: правка владельца не должна пропасть без ключа Gemini.
        inherited = _inherit_meta(store, fresh)
        if inherited:
            return {"status": "inherited", **inherited}
        if not auto_meta_enabled(store):
            return {"status": "skipped", "reason": "выключено"}
        meta = generate_publish_metadata(store, clip_id, client=client or GeminiClient(timeout=90))
        record = save_publish_meta(store, clip_id, meta, source="auto")
        return {"status": "generated", "title": record["title"]}
    except Exception as exc:  # noqa: BLE001 - метаданные не ломают рендер
        _log.warning("auto publish meta for clip %s failed: %s", clip_id, str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}


def _inherit_meta(store: Any, clip: dict) -> dict | None:
    """Прежний рендер того же клипа: его метаданные или ручной заголовок — на новый рендер."""
    plan_id = clip.get("clip_plan_id")
    if not plan_id:
        return None
    try:
        plan = store.get_clip_plan(int(plan_id), include_segments=False)
        # Весь клип, а не один план: новая версия монтажа — новый план той же группы (app/clip_groups.py).
        from app.clip_groups import group_members

        members = [int(m["id"]) for m in group_members(store, int(plan_id))]
        previous = [c for c in store.list_clips_for_plans(members)
                    if c.get("status") == "succeeded" and int(c.get("id") or 0) < int(clip["id"])]
    except Exception:  # noqa: BLE001 - нет прежних — генерируем
        return None
    if not previous:
        return None
    prev = previous[0]  # самый свежий прежний рендер (список — новые первыми)
    title = str(prev.get("title") or "").strip()
    if prev.get("publish_meta"):
        record = dict(prev["publish_meta"])
    elif title and title != str(plan.get("title") or "").strip():
        record = {"title": title, "description": prev.get("description") or "", "hashtags": [], "platforms": {},
                  "cover_hooks": [], "model": "", "source": "manual", "generated_at": _now()}
    else:
        return None
    store.update_clip(int(clip["id"]), title=title or record["title"], description=prev.get("description") or "")
    store.set_clip_publish_meta(int(clip["id"]), {**record, "inherited_from": int(prev["id"])})
    return {"from_clip": int(prev["id"]), "title": title or record["title"]}


def apply_ai_metadata(store: Any, clip_ids: list[int], workers: int = 4, only_missing: bool = False) -> dict[int, str]:
    """Generate metadata for several clips in parallel and SAVE it on each clip
    (title; description with hashtags appended; variants in ``publish_meta``). Best effort:
    a clip whose generation fails keeps its current title — returns {clip_id: error}.
    ``only_missing`` — skip clips that already have metadata (written after render)."""
    from concurrent.futures import ThreadPoolExecutor

    errors: dict[int, str] = {}

    def one(clip_id: int) -> None:
        try:
            if only_missing and store.get_clip(clip_id).get("publish_meta"):
                return
            meta = generate_publish_metadata(store, clip_id)
            save_publish_meta(store, clip_id, meta, source="ai")
        except Exception as exc:  # noqa: BLE001 - never block publishing on metadata
            errors[clip_id] = str(exc)[:200]

    ids = list(dict.fromkeys(int(c) for c in clip_ids))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ids) or 1))) as pool:
        list(pool.map(one, ids))
    return errors
