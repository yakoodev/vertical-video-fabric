"""✨ ИИ выбирает лучшие: the model picks the most shareable moments to star.

A long stream yields hundreds of candidates; nobody reads 228 cards. The model
gets a compact line per visible moment — title, description, category, length,
the analysis quality score and the first spoken words — and returns the best N,
skipping near-duplicates (several analyses often find the same moment). Picks
are starred (additively: the owner's stars are never removed) and each keeps a
score and a one-line reason, shown on its card.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

SPEECH_CHARS = 220
MAX_CANDIDATES = 400

RULES = """\
Ты — редактор шортсов (YouTube Shorts / TikTok / Reels). Ниже — кандидаты на клипы из одного
длинного видео. Выбери {n} лучших — тех, что с наибольшей вероятностью досмотрят и перешлют:
сильный хук в первые секунды, законченная мысль/шутка/реакция, эмоция, понятно без контекста.
Не бери смысловые дубли: если несколько кандидатов про один и тот же момент (пересекаются по
времени или смыслу) — возьми один, лучший. Штрафуй: скучное, обрывки без развязки, «вода».
Для каждого выбранного: id, score 1–10, reason — одна короткая фраза по-русски, почему зайдёт.
Отсортируй по убыванию score. Верни ТОЛЬКО JSON по схеме.
{goal}
Кандидаты (JSON, по строке на момент):
"""


def _schema() -> dict[str, Any]:
    pick = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "score": {"type": "number"}, "reason": {"type": "string"}},
        "required": ["id", "score", "reason"],
        "propertyOrdering": ["id", "score", "reason"],
    }
    return {
        "type": "object",
        "properties": {"picks": {"type": "array", "items": pick}},
        "required": ["picks"],
    }


def candidate_lines(store: Any, source_id: int) -> list[dict]:
    plans = [p for p in store.list_clip_plans(source_id=source_id) if not p.get("hidden")]
    try:
        cues = store.get_source_transcript(source_id)
    except KeyError:
        cues = []
    out = []
    for plan in plans[:MAX_CANDIDATES]:
        segs = plan.get("segments") or []
        if not segs:
            continue
        ranges = [(float(s["start_sec"]), float(s["end_sec"])) for s in segs]
        words = []
        for cue in cues:
            try:
                a, b = float(cue["start"]), float(cue["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(a < e and b > s for s, e in ranges):
                words.append(str(cue.get("text") or "").strip())
            if sum(len(w) for w in words) > SPEECH_CHARS:
                break
        out.append(
            {
                "id": plan["id"],
                "t": round(ranges[0][0]),
                "sec": round(sum(e - s for s, e in ranges)),
                "title": plan.get("title") or "",
                "about": (plan.get("description") or "")[:200],
                "cat": plan.get("category") or "",
                "q": round(float(plan.get("quality") or 0), 2),
                "speech": " ".join(words)[:SPEECH_CHARS],
                "dup": plan.get("duplicate_of") is not None,
            }
        )
    return out


def pick_best(
    store: Any,
    source_id: int,
    count: int = 10,
    goal: str = "",
    client: GeminiClient | None = None,
    model: str | None = None,
) -> dict:
    """Ask the model, star the picks, remember score + reason. Returns the picks."""
    candidates = candidate_lines(store, source_id)
    if not candidates:
        raise RuntimeError("нет видимых моментов — сначала анализ")
    n = max(1, min(int(count), 50, len(candidates)))
    prompt = RULES.format(n=n, goal=f"Пожелание владельца: {goal.strip()}\n" if goal.strip() else "")
    prompt += "\n".join(json.dumps(c, ensure_ascii=False) for c in candidates)
    used_model = model or settings.gemini_text_model
    response = (client or GeminiClient()).generate_content(
        used_model,
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json", "responseSchema": _schema()},
        },
    )
    try:
        raw = json.loads(_extract_response_text(response)).get("picks") or []
    except json.JSONDecodeError as exc:
        raise RuntimeError("модель вернула не-JSON") from exc
    known = {c["id"] for c in candidates}
    picks, seen = [], set()
    for item in raw:
        try:
            pid = int(item["id"])
            score = max(1.0, min(10.0, float(item.get("score") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
        if pid not in known or pid in seen:
            continue  # never star something the model made up
        seen.add(pid)
        picks.append({"id": pid, "score": round(score, 1), "reason": str(item.get("reason") or "").strip()[:200]})
        if len(picks) >= n:
            break
    if not picks:
        raise RuntimeError("ИИ никого не выбрал")
    for p in picks:
        store.set_clip_plan_flags(p["id"], favorite=True)
        store.set_clip_plan_ai_pick(p["id"], {"score": p["score"], "reason": p["reason"]})
    return {"picks": picks, "model": used_model, "candidates": len(candidates)}
