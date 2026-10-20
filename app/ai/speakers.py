"""Кто говорит: разметка голосов в субтитрах клипа ушами Gemini.

Владелец: «понимать, что в видео два голоса разных людей, чтобы субтитры делать
разного цвета и не смешивать их». В стримах это гость в войсе, озвучка доната,
ролик, который смотрит стример. Gemini слушает аудио клипа и по пронумерованным
словам субтитров говорит, где чей голос. Главный (стример) — A, остальные — B, C…

Метка ложится в слова субтитров (``word["speaker"]``), дальше её несут
``caption_words`` → ``comp.words[].s`` → слой captions (свой цвет, своя страница).
Если голос один — меток нет. Результат кэшируется по словам: модель зовём один раз.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

MAX_SPEAKERS = 4
LETTERS = "ABCD"

PROMPT = """\
Это аудио вертикального клипа со стрима и его субтитры по словам: «номер [секунда] слово».
Определи, сколько РАЗНЫХ людей говорит, и кому принадлежит каждое слово.
A — главный голос клипа (обычно стример, говорит больше всех); B, C, D — другие: гость,
озвучка доната (TTS), голос из ролика, который смотрят. Смех и выкрики — тому, кто их издал.
Ответь ТОЛЬКО JSON:
{"speakers": {"A": "кто это, 2–4 слова", "B": "…"}, "turns": [{"from": 0, "to": 12, "s": "A"}, …]}
turns — подряд идущие отрезки номеров слов (from и to включительно), покрывающие все слова.
Если голос один — {"speakers": {"A": "…"}, "turns": [{"from": 0, "to": последний, "s": "A"}]}.
Не выдумывай второго человека: интонация и эмоции одного человека — это один голос.
"""


def _words_key(words: list[dict]) -> str:
    raw = json.dumps([(w.get("word"), round(float(w.get("start", 0)), 2)) for w in words], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def label_speakers(store: Any, clip_plan_id: int, client: Any = None, *, force: bool = False) -> dict:
    """Разметить голоса в субтитрах клипа. Возвращает ``{speakers, counts, changed}``.

    Метки пишутся в сохранённые субтитры клипа. Уже размеченные теми же словами —
    второй раз модель не зовём (если не ``force``).
    """
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = [dict(w) for w in payload.get("words") or [] if str(w.get("word") or "").strip()]
    if not words:
        raise ValueError("у клипа нет распознанной речи — сначала распознайте субтитры")
    key = _words_key(words)
    if not force and payload.get("speakers_key") == key:
        return {"speakers": payload.get("speakers") or {}, "counts": _counts(words), "changed": False}
    if client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("Gemini не настроен — разметить голоса нечем")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    audio = _clip_audio(store, clip_plan_id)
    listing = "\n".join(f"{i} [{float(w['start']):.1f}] {w['word']}" for i, w in enumerate(words))
    response = client.generate_content(
        settings.gemini_text_model,
        {
            "contents": [{"role": "user", "parts": [
                {"text": PROMPT},
                {"inlineData": {"mimeType": "audio/mp3", "data": base64.b64encode(audio).decode()}},
                {"text": listing},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        },
    )
    text = "".join(
        p.get("text", "") for p in ((response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    )
    speakers, labels = parse_turns(text, len(words))
    if len(set(labels)) <= 1:
        labels, speakers = [""] * len(words), {k: v for k, v in speakers.items() if k == "A"}
    for word, label in zip(words, labels):
        if label:
            word["speaker"] = label
        else:
            word.pop("speaker", None)
    store.set_clip_plan_subtitles(
        clip_plan_id, {**payload, "words": words, "speakers": speakers, "speakers_key": key}
    )
    return {"speakers": speakers, "counts": _counts(words), "changed": True}


def parse_turns(text: str, total: int) -> tuple[dict, list[str]]:
    """Ответ модели → (описания голосов, метка на каждое слово). Дыры — соседней меткой,
    незнакомые буквы и лишние голоса — главному."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except ValueError:
        return {}, [""] * total
    allowed = set(LETTERS[:MAX_SPEAKERS])
    speakers = {
        str(k).strip().upper()[:1]: str(v)[:60]
        for k, v in (data.get("speakers") or {}).items()
        if str(k).strip().upper()[:1] in allowed
    }
    labels: list[str] = [""] * total
    for turn in data.get("turns") or []:
        try:
            lo, hi = int(turn["from"]), int(turn["to"])
        except (KeyError, TypeError, ValueError):
            continue
        s = str(turn.get("s") or "A").strip().upper()[:1]
        s = s if s in allowed else "A"
        for i in range(max(0, lo), min(total - 1, hi) + 1):
            labels[i] = s
    last = "A"
    for i, label in enumerate(labels):
        labels[i] = label or last
        last = labels[i]
    return speakers, labels


def _counts(words: list[dict]) -> dict:
    out: dict[str, int] = {}
    for w in words:
        s = w.get("speaker") or "A"
        out[s] = out.get(s, 0) + 1
    return out


def _clip_audio(store: Any, clip_plan_id: int) -> bytes:
    """Звук клипа (куски подряд, как у субтитров) — mp3 моно: Gemini хватает, а весит мало."""
    from app.clip_subtitles import build_clip_audio_args
    from app.render import _run_ffmpeg

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    out = settings.tmp_dir / f"speakers-{uuid4().hex}.mp3"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, out), timeout=300)
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
