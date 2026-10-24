"""Publishing metadata for a clip: title, description and hashtags.

Text-only Gemini call fed with what the clip actually contains — the moment's
title/description/category and the Whisper transcript lines inside the clip's
segments — steered by the default «publishing» prompt preset (editable in
Settings → Промпты). Cheap: a few hundred input tokens on a flash model.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

MAX_TITLE = 95  # YouTube caps titles at 100 chars
MAX_TRANSCRIPT_CHARS = 4000

BASE_RULES = """\
Ты пишешь метаданные для публикации вертикального клипа (YouTube Shorts, TikTok, Instagram Reels).
Язык — русский (как в клипе). Верни ТОЛЬКО JSON по схеме.
- title: до 90 символов, конкретно про то, что происходит в клипе, цепляет, без капса и без вранья о содержании.
- description: 1–3 коротких предложения, раскрывают момент и зовут досмотреть; без хэштегов внутри.
- hashtags: 5–10 штук, каждый начинается с #, без пробелов, строчными; сначала 2–4 по теме клипа, затем общие (#shorts, #стрим, #нарезка и т.п. по смыслу).
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
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "description", "hashtags"],
        "propertyOrdering": ["title", "description", "hashtags"],
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


def _clean_hashtags(raw: Any) -> list[str]:
    out: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        t = re.sub(r"\s+", "", str(tag or "")).lstrip("#").lower()
        t = re.sub(r"[^\w]", "", t)
        if t and f"#{t}" not in out:
            out.append(f"#{t}")
    return out[:10]


def parse_metadata(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("модель вернула не-JSON") from exc
    title = " ".join(str(data.get("title") or "").split())[:MAX_TITLE].strip()
    description = str(data.get("description") or "").strip()
    if not title:
        raise RuntimeError("модель не дала заголовок")
    return {"title": title, "description": description, "hashtags": _clean_hashtags(data.get("hashtags"))}


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
    prompt = (
        rules_for_format(fmt)
        + (f"\nДополнительные указания (пресет «{preset.get('label')}»):\n{user_rules}\n" if user_rules else "")
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
    return meta


def apply_ai_metadata(store: Any, clip_ids: list[int], workers: int = 4) -> dict[int, str]:
    """Generate metadata for several clips in parallel and SAVE it on each clip
    (title; description with hashtags appended). Best effort: a clip whose
    generation fails keeps its current title — returns {clip_id: error}."""
    from concurrent.futures import ThreadPoolExecutor

    errors: dict[int, str] = {}

    def one(clip_id: int) -> None:
        try:
            meta = generate_publish_metadata(store, clip_id)
            description = "\n\n".join(x for x in (meta["description"], " ".join(meta["hashtags"])) if x)
            store.update_clip(clip_id, title=meta["title"], description=description)
        except Exception as exc:  # noqa: BLE001 - never block publishing on metadata
            errors[clip_id] = str(exc)[:200]

    ids = list(dict.fromkeys(int(c) for c in clip_ids))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ids) or 1))) as pool:
        list(pool.map(one, ids))
    return errors
