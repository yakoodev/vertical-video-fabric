"""Эмфаза отдельного слова в субтитрах: ключевое слово крупнее и «выпрыгивает» (pop), на
сильной — ещё и встряска, как у топовых каналов (не только цвет ``highlightWords``).

Хранится прямо в слове субтитров клипа — ``subtitles_json.words[i].emph``: 1 — ``pop``
(×1.25 и прыжок), 2 — ``shake`` (×1.45, прыжок и встряска). Метка едет вместе со словом при
любых пересчётах (вырезы, J/L, переходы, скорость кусков), а слою не нужен номер слова.

* Слой ``captions`` (моушн, превью == рендер): ``comp.words[k].e`` — уровень; кегль слова ×
  ``emphScale`` слоя (у уровня 2 — сильнее), цвет ``emphColor`` (нет — активный цвет стиля),
  pop на его момент, у уровня 2 — затухающая встряска (``frontend/src/motion/layers/captions.ts``).
* Прожигаемые субтитры (ASS, ``app/subtitles/ass.py``): ``\\fscx/\\fscy`` на всю страницу
  (раскладка не прыгает) + ``\\t``-прыжок в момент слова; встряски в ASS нет.

Подбор «ключевого слова строки» (``auto``): одно на строку — число, слово с «!», иначе самое
длинное не служебное слово (≥ 4 букв). Gemini — по желанию (``pick: "gemini"``), не вышло —
та же эвристика.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.settings import settings

LEVELS = {"off": 0, "none": 0, "pop": 1, "shake": 2}
MAX_LEVEL = 2

# Служебные слова — не «ключевые», даже если длинные.
STOPWORDS = frozenset("""
и а но да или либо что чтобы как так там тут это этот эта эти тот та те то вот вон уже ещё еще
же ли бы не ни нет вы мы ты он она оно они я меня мне тебя тебе его ему её ее ей их им нас вас
нам вам себя себе свой своя свои своё свое мой моя мои твой твоя наш наша ваш ваша который
которая которые когда тогда потом если ведь даже только просто очень можно нужно надо будет
было были была быть есть может могу можешь хочу хочешь вообще короче типа щас сейчас ну эм ээ
кстати просто значит именно где куда откуда почему зачем сколько какой какая какие такой такая
такие тоже также всё все всех всем всего между после перед через около потому поэтому
the a an and or but so to of in on at for with is are was were be been it this that these those
you we they he she i me my your our their his her its just like really very what when where why
how there here then than not no yes do does did have has had will would can could should
""".split())

_NUMBER = re.compile(r"\d")
_LETTERS = re.compile(r"[^\wё-]+", re.IGNORECASE)


def level_of(value: Any) -> int:
    """Уровень эмфазы 0..2 из числа или имени (pop / shake / off)."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key in LEVELS:
            return LEVELS[key]
        try:
            value = float(key)
        except ValueError:
            return 0
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_LEVEL, number))


def _bare(word: Any) -> str:
    return _LETTERS.sub("", str(word or "").lower().replace("ё", "е")).strip("-")


def key_word_score(word: str) -> float:
    """Насколько слово годится в «ключевое»: 0 — никак."""
    raw = str(word or "").strip()
    bare = _bare(raw)
    if not bare:
        return 0.0
    if _NUMBER.search(raw):
        return 100.0 + len(bare)  # цифры, суммы, «100%» — всегда главное в строке
    if bare in STOPWORDS:
        return 0.0
    score = 0.0
    if raw.endswith("!") or raw.endswith("!?") or raw.endswith("?!"):
        score += 50.0
    if raw.isupper() and len(bare) >= 2:
        score += 20.0  # уже набран капсом в расшифровке — крик
    if len(bare) >= 4:
        score += len(bare)
    elif score == 0.0:
        return 0.0
    return score


def pick_heuristic(lines: list[list[dict]]) -> list[int]:
    """По строке — номер ключевого слова (в общем списке слов) или ничего. ``lines`` —
    слова строк, у каждого ``i`` (номер в словах субтитров) и ``word``."""
    out = []
    for words in lines:
        best, best_score = None, 0.0
        for w in words:
            score = key_word_score(w.get("word"))
            if score > best_score:
                best, best_score = w["i"], score
        if best is not None and (len(words) >= 2 or best_score >= 50):
            out.append(best)
    return out


def gemini_pick(lines: list[list[dict]], client: Any = None, model: str | None = None) -> list[int]:
    """Gemini выбирает по слову на строку (одним запросом). RuntimeError — выбрать нечем."""
    if client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("Gemini не настроен")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    from app.ai.gemini import _extract_response_text

    rows = [{"line": n, "words": [w["word"] for w in words]} for n, words in enumerate(lines) if words]
    prompt = (
        "Ты монтажёр коротких вертикальных роликов. Для каждой строки субтитров выбери ОДНО ключевое слово, "
        "которое стоит выделить крупно (смысловой удар: число, эмоция, неожиданное слово, панчлайн). "
        "Служебные слова не выбирай; если в строке нечего выделять — пропусти её. Ответ — JSON-массив "
        '[{"line": номер строки, "word": номер слова в строке с 0}].\n\n'
        + json.dumps(rows, ensure_ascii=False)
    )
    response = client.generate_content(
        model or settings.gemini_text_model,
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}},
    )
    try:
        data = json.loads(_extract_response_text(response))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gemini вернул не JSON") from exc
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            words = lines[int(item["line"])]
            out.append(words[int(item["word"])]["i"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    if not out:
        raise RuntimeError("Gemini ничего не выбрал")
    return out


def line_words(payload: dict) -> list[list[dict]]:
    """Слова субтитров по строкам (каждое — в одной строке) с номером ``i`` в ``payload.words``."""
    from app.clip_subtitles import words_by_line

    words = payload.get("words") or []
    indexed = [{**w, "i": i} for i, w in enumerate(words)]
    lines = payload.get("lines") or []
    if not lines:
        return [indexed] if indexed else []
    return words_by_line(lines, indexed)


def emphasized(payload: dict | None) -> list[dict]:
    """Слова с эмфазой: ``[{i, word, emph}]``."""
    return [{"i": i, "word": w.get("word"), "emph": level_of(w.get("emph"))}
            for i, w in enumerate((payload or {}).get("words") or []) if level_of(w.get("emph"))]


def apply_levels(words: list[dict], levels: dict[int, int]) -> list[dict]:
    """Слова с новыми уровнями: 0 — снять метку."""
    out = []
    for i, w in enumerate(words):
        w = dict(w)
        if i in levels:
            if levels[i]:
                w["emph"] = levels[i]
            else:
                w.pop("emph", None)
        out.append(w)
    return out
