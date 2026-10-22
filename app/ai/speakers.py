"""Голоса клипа: кто что сказал — ушами Gemini, время — от whisper.

Владелец: «понимать, что в видео два голоса разных людей, чтобы субтитры делать
разного цвета и не смешивать их» и «проёбывается фраза стримерши из-за голоса на
видео». В стримах поверх стримера говорят гость в войсе, озвучка доната, ролик,
который он смотрит. Whisper слышит одну дорожку: перебитую фразу теряет или
склеивает два голоса в одну строку.

Gemini слушает звук клипа и расшифровывает КАЖДЫЙ голос отдельно (реплики
``{s, start, end, text}``), в том числе когда говорят одновременно. Дальше
``merge_voices`` сшивает это со словами whisper: где слово совпало — берём точное
время whisper, где whisper слово потерял — время интерполируем внутри реплики.
Главный голос (стример) — A, остальные — B, C, D.

Метка ложится в слова субтитров (``word["speaker"]``), дальше её несут
``caption_words`` → ``comp.words[].s`` → слой captions (свой цвет, своя страница,
фильтр ``speakers`` — отдельный слой для других голосов). Голос один — меток нет.
Результат кэшируется по словам: модель зовём один раз.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

LETTERS = "ABCD"
# Реплики Gemini дают слов сильно меньше whisper — значит, модель недослушала:
# не портим субтитры, оставляем как было.
MIN_COVERAGE = 0.6
LINE_MAX_WORDS = 7
LINE_PAUSE = 0.6

PROMPT = """\
Это звук вертикального клипа со стрима. Ниже — черновые субтитры whisper по словам
(«[секунда] слово»): время у них точное, но whisper слышит одну дорожку — он теряет слова,
когда говорят одновременно, и склеивает разных людей.
Расшифруй КАЖДЫЙ голос отдельно, дословно, по-русски, с матом как есть:
A — главный голос клипа (обычно стример, говорит больше всех); B, C, D — другие: гость,
озвучка доната (TTS), голос из ролика или игры, который звучит на стриме.
Когда два голоса звучат одновременно — дай обе реплики, их время может пересекаться.
Не выдумывай второго человека: интонации и эмоции одного человека — это один голос.
Пение и музыку без слов пропускай.
Ответь ТОЛЬКО JSON:
{"speakers": {"A": "кто это, 2–4 слова", "B": "…"},
 "utterances": [{"s": "A", "start": 0.0, "end": 2.4, "text": "…"}, …]}
start/end — секунды клипа; реплики — по порядку времени, короткие (одна фраза).
"""


# --------------------------------------------------------------------------- #
# Сшивка
# --------------------------------------------------------------------------- #


def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower().replace("ё", "е"))


def parse_utterances(text: str) -> tuple[dict, list[dict]]:
    """Ответ модели → (описания голосов, реплики {s, start, end, text}). Мусор — пусто."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except ValueError:
        return {}, []
    allowed = set(LETTERS)
    speakers = {
        str(k).strip().upper()[:1]: str(v)[:60]
        for k, v in (data.get("speakers") or {}).items()
        if str(k).strip().upper()[:1] in allowed
    }
    out = []
    for u in data.get("utterances") or []:
        try:
            s0, s1 = float(u["start"]), float(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        words = str(u.get("text") or "").split()
        if not words:
            continue
        s = str(u.get("s") or "A").strip().upper()[:1]
        out.append({"s": s if s in allowed else "A", "start": max(0.0, s0), "end": max(s0 + 0.1, s1), "text": " ".join(words)})
    out.sort(key=lambda u: u["start"])
    return speakers, out


def merge_voices(whisper: list[dict], utterances: list[dict], slack: float = 0.6) -> list[dict]:
    """Слова whisper + реплики Gemini → слова с меткой голоса.

    Для каждой реплики ищем её слова среди слов whisper рядом по времени (по
    порядку, нечётко). Совпавшее слово берёт время whisper, пропущенное whisper'ом —
    время между соседями внутри реплики. Слова whisper, которые не вошли ни в одну
    реплику, остаются (метка — ближайшей реплики), если лежат вне реплик, и
    выбрасываются, если внутри (это был тот же текст, услышанный криво).
    """
    used = [False] * len(whisper)
    out: list[dict] = []
    for u in utterances:
        tokens = u["text"].split()
        near = [
            i for i, w in enumerate(whisper)
            if not used[i] and u["start"] - slack <= float(w["start"]) <= u["end"] + slack
        ]
        sm = difflib.SequenceMatcher(
            a=[_norm(t) for t in tokens], b=[_norm(str(whisper[i]["word"])) for i in near], autojunk=False
        )
        timing: list[tuple[float, float] | None] = [None] * len(tokens)
        for block in sm.get_matching_blocks():
            for k in range(block.size):
                wi = near[block.b + k]
                used[wi] = True
                timing[block.a + k] = (float(whisper[wi]["start"]), float(whisper[wi]["end"]))
        out.extend(_fill_timing(tokens, timing, u))
    covered = [(u["start"], u["end"]) for u in utterances]
    for i, w in enumerate(whisper):
        if used[i]:
            continue
        t = float(w["start"])
        if any(a - 0.05 <= t <= b + 0.05 for a, b in covered):
            continue
        nearest = min(utterances, key=lambda u: min(abs(u["start"] - t), abs(u["end"] - t)), default=None)
        out.append({"word": str(w["word"]), "start": float(w["start"]), "end": float(w["end"]),
                    "speaker": nearest["s"] if nearest else "A"})
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def _fill_timing(tokens: list[str], timing: list, u: dict) -> list[dict]:
    """Время слов реплики: известные — от whisper, пропуски — поровну между соседями."""
    words: list[dict] = []
    k = 0
    while k < len(tokens):
        if timing[k] is not None:
            a, b = timing[k]
            words.append({"word": tokens[k], "start": round(a, 3), "end": round(b, 3), "speaker": u["s"]})
            k += 1
            continue
        j = k
        while j < len(tokens) and timing[j] is None:
            j += 1
        n = j - k
        right = timing[j][0] if j < len(tokens) else max(u["end"], (words[-1]["end"] if words else u["start"]) + 0.2 * n)
        if words:
            left = words[-1]["end"]
        else:
            # Начало реплики whisper не расслышал: слова встают ДО первого совпавшего,
            # даже если Gemini поставил начало реплики чуть позже.
            left = max(0.0, min(u["start"], right - 0.2 * n))
        if right <= left:
            right = left + 0.04 * n  # зажаты между соседями — порядок важнее длительности
        step = (right - left) / n
        for m in range(k, j):
            a = left + step * (m - k)
            words.append({"word": tokens[m], "start": round(a, 3), "end": round(a + step * 0.9, 3), "speaker": u["s"]})
        k = j
    return words


_SENTENCE_END = re.compile(r"[.!?…]+[»\"')\]]*$")


def lines_from_words(words: list[dict]) -> list[dict]:
    """Строки субтитров из слов: новая строка на смене голоса, конце предложения
    (. ! ? …), паузе больше LINE_PAUSE и по длине — реплики разных людей и разные
    фразы не попадают в одну строку (агент-монтажёр: «ДТП. Бывает. Ну,» одной строкой)."""
    lines: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            lines.append({"start": round(cur[0]["start"], 3), "end": round(cur[-1]["end"], 3),
                          "text": " ".join(w["word"] for w in cur), "speaker": cur[0].get("speaker") or "A"})
            cur.clear()

    for w in sorted(words, key=lambda w: w["start"]):
        if cur and (
            w.get("speaker") != cur[-1].get("speaker")
            or _SENTENCE_END.search(str(cur[-1]["word"]).strip())
            or float(w["start"]) - float(cur[-1]["end"]) > LINE_PAUSE
            or len(cur) >= LINE_MAX_WORDS
        ):
            flush()
        cur.append(w)
    flush()
    # Строки одного голоса не перекрываются (редактор строк этого не любит).
    for a, b in zip(lines, lines[1:]):
        if a["speaker"] == b["speaker"] and a["end"] > b["start"]:
            a["end"] = b["start"]
    return [l for l in lines if l["end"] > l["start"]]


# --------------------------------------------------------------------------- #
# Инструмент
# --------------------------------------------------------------------------- #


def _words_key(words: list[dict]) -> str:
    raw = json.dumps([(w.get("word"), round(float(w.get("start", 0)), 2)) for w in words], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def separate_voices(
    store: Any, clip_plan_id: int, client: Any = None, *, force: bool = False, retranscribe: bool = False
) -> dict:
    """Разобрать голоса клипа и переписать субтитры: у каждого слова — чей голос,
    потерянные whisper'ом слова — на месте. Возвращает
    ``{speakers, counts, changed, added, retranscribed, warning?}``.

    ``retranscribe`` — сначала распознать речь точной моделью (large-v3-turbo), если
    субтитры сделаны более слабой: время слов берётся у whisper, и от small под
    чужими голосами толку мало."""
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    retranscribed = False
    if retranscribe:
        from app.clip_subtitles import generate_for_plan
        from app.subtitles.registry import QUALITY_WHISPER_MODEL

        if payload.get("model") != QUALITY_WHISPER_MODEL or not payload.get("words"):
            payload = generate_for_plan(store, clip_plan_id, provider="whisper", model=QUALITY_WHISPER_MODEL)
            retranscribed, force = True, True
    words = [dict(w) for w in payload.get("words") or [] if str(w.get("word") or "").strip()]
    if not words:
        raise ValueError("у клипа нет распознанной речи — сначала распознайте субтитры")
    if not force and payload.get("speakers_key") and payload.get("speakers_key") == _words_key(words):
        return {"speakers": payload.get("speakers") or {}, "counts": _counts(words), "changed": False, "added": 0,
                "retranscribed": False}
    if client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("Gemini не настроен — разобрать голоса нечем")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    # Черновик whisper без старых меток: модель слышит всё заново.
    draft = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in words]
    audio = _clip_audio(store, clip_plan_id)
    listing = "\n".join(f"[{w['start']:.1f}] {w['word']}" for w in draft)
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
    speakers, utterances = parse_utterances(text)
    said = sum(len(u["text"].split()) for u in utterances)
    if not utterances or said < MIN_COVERAGE * len(draft):
        return {"speakers": speakers, "counts": _counts(words), "changed": retranscribed, "added": 0,
                "retranscribed": retranscribed,
                "warning": f"модель расслышала {said} слов из {len(draft)} — голоса не разбираю"}
    merged = merge_voices(draft, utterances)
    if len({w["speaker"] for w in merged}) <= 1:
        for w in merged:
            w.pop("speaker", None)
        speakers = {k: v for k, v in speakers.items() if k == "A"}
    lines = lines_from_words(merged)
    fresh = {**payload, "words": merged, "lines": lines, "speakers": speakers, "edited": True}
    fresh["speakers_key"] = _words_key(merged)
    store.set_clip_plan_subtitles(clip_plan_id, fresh)
    return {"speakers": speakers, "counts": _counts(merged), "changed": True,
            "added": max(0, len(merged) - len(draft)), "retranscribed": retranscribed}


def assign_voice(
    store: Any, clip_plan_id: int, start: float, end: float, speaker: str, description: str | None = None
) -> dict:
    """Отдать слова, начинающиеся в [start, end), голосу ``speaker`` — правка ошибки
    модели руками. Строки пересобираются; голос один — метки снимаются."""
    speaker = str(speaker).strip().upper()[:1]
    if speaker not in LETTERS:
        raise ValueError("голос — одна из букв A, B, C, D")
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = [dict(w) for w in payload.get("words") or []]
    hit = 0
    for w in words:
        if start - 1e-6 <= float(w["start"]) < end:
            w["speaker"] = speaker
            hit += 1
    if not hit:
        raise ValueError("в этом отрезке нет слов")
    labels = {w.get("speaker") or "A" for w in words}
    if labels == {"A"}:
        for w in words:
            w.pop("speaker", None)
    else:
        for w in words:
            w.setdefault("speaker", "A")
    speakers = dict(payload.get("speakers") or {})
    if description:
        speakers[speaker] = description[:60]
    speakers.setdefault(speaker, "голос " + speaker)
    used = set(_counts(words))
    speakers = {k: v for k, v in speakers.items() if k in used}
    fresh ={**payload, "words": words, "lines": lines_from_words(words), "speakers": speakers, "edited": True}
    fresh["speakers_key"] = _words_key(words)
    store.set_clip_plan_subtitles(clip_plan_id, fresh)
    return {"speakers": speakers, "counts": _counts(words), "moved": hit, "subtitles": fresh}


# Прежнее имя (агентский инструмент, автомонтаж).
label_speakers = separate_voices


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
    from app.source_audio import source_mix

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    source_path = Path(str(source.get("local_path") or ""))
    if not source_path.exists():
        raise ValueError("исходник недоступен")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    out = settings.tmp_dir / f"speakers-{uuid4().hex}.mp3"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, out, source_mix(source)), timeout=300)
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
