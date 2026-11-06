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

Время реплик у модели ненадёжно: дальше первой минуты она пишет то секунды, то «1:23» как 123.0
(каждая минута даёт +40 с). Из-за этого в сборке Муры на 214-секундном клипе те же фразы встали на
40 и 80 с позже, а слова вылезли за конец клипа. ``fit_times`` сверяет реплики со словами whisper
и возвращает их на место. Клип длиннее ``WINDOW_SEC`` слушаем окнами с перекрытием
(``plan_windows``); буквы между окнами сводит ``_LetterBook``.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
from bisect import bisect_left
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings
from app.subtitles.phantoms import is_phantom_text

LETTERS = "ABCD"
# Реплики Gemini дают слов сильно меньше whisper — значит, модель недослушала:
# не портим субтитры, оставляем как было.
MIN_COVERAGE = 0.6
LINE_MAX_WORDS = 7
LINE_PAUSE = 0.6
# Дольше — слушаем окнами: длинный звук модель разбирает хуже и может оборвать ответ.
WINDOW_SEC = 180.0
WINDOW_OVERLAP = 12.0
# Реплика не на месте, если рядом с её временем whisper слышал меньше этой доли её слов.
PLACED_SHARE = 0.5
# Опора времени — столько слов подряд совпало с whisper: случайно такого почти не бывает.
ANCHOR_RUN = 3
SHIFT_MIN_SEC = 1.0
# Со словами whisper совпало меньше этой доли — ответ не про этот звук, субтитры не трогаем.
MIN_HEARD = 0.25
# Голоса окон сравниваем отпечатками: свой голос 0.62–0.84, чужой до 0.49 (оценка на стриме Муры).
SAME_VOICE_SCORE = 0.55
# Озвучка чата похожа на себя слабее (0.46–0.54): та же буква в окне — достаточно этого и отрыва от остальных.
SAME_LETTER_SCORE = 0.45
SAME_LETTER_MARGIN = 0.1

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
start/end — число секунд от начала этого звука: 83.5, а не 1:23 и не 123.5.
Реплики — по порядку времени, короткие (одна фраза).
"""


# --------------------------------------------------------------------------- #
# Сшивка
# --------------------------------------------------------------------------- #


def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower().replace("ё", "е"))


def _seconds(value: Any) -> float:
    """Секунды реплики; модель иногда отвечает строкой «1:23.5»."""
    if isinstance(value, str) and ":" in value:
        total = 0.0
        for part in value.strip().split(":"):
            total = total * 60 + float(part)
        return total
    return float(value)


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
            s0, s1 = _seconds(u["start"]), _seconds(u["end"])
        except (KeyError, TypeError, ValueError):
            continue
        words = str(u.get("text") or "").split()
        if not words or is_phantom_text(" ".join(words)):
            continue  # «Продолжение следует…» на тишине — модель дослушала титры, а не речь
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


def _heard_near(tokens: list[str], whisper: list[dict], start: float, end: float, slack: float = 0.6) -> int:
    """Сколько слов реплики whisper слышал рядом с её временем."""
    near = [_norm(str(w["word"])) for w in whisper if start - slack <= float(w["start"]) <= end + slack]
    if not near:
        return 0
    blocks = difflib.SequenceMatcher(a=[_norm(t) for t in tokens], b=near, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks)


def _heard_total(utterances: list[dict], whisper: list[dict]) -> int:
    return sum(_heard_near(u["text"].split(), whisper, u["start"], u["end"]) for u in utterances)


def _from_minutes(value: float) -> float:
    """123.5 в записи «минуты и две цифры секунд» → 83.5."""
    minutes = int(value // 100)
    return minutes * 60 + value - minutes * 100


def _undo_minute_notation(utterances: list[dict], whisper: list[dict], duration: float) -> tuple[list[dict], bool]:
    """Весь ответ в записи 1:23 → 123: секунд в ней не бывает больше 59. Решают слова whisper."""
    values = [v for u in utterances for v in (u["start"], u["end"])]
    if not values or max(values) < 60 or any(v % 100 >= 60 for v in values):
        return utterances, False
    fixed = [{**u, "start": _from_minutes(u["start"]), "end": _from_minutes(u["end"])} for u in utterances]
    raw_heard, fixed_heard = _heard_total(utterances, whisper), _heard_total(fixed, whisper)
    if fixed_heard > raw_heard or (fixed_heard == raw_heard and max(values) > duration + 1):
        return fixed, True
    return utterances, False


def _anchor_times(utterances: list[dict], whisper: list[dict]) -> tuple[list[dict], int]:
    """Реплики не на своём месте сдвинуть туда, где whisper слышал те же слова подряд.

    Реплика, которую whisper не слышал совсем, — это потерянная им фраза под чужим голосом: своё
    время у неё остаётся, если ближайшую реплику двигать не пришлось."""
    tokens: list[tuple[int, float, str]] = []
    for index, u in enumerate(utterances):
        words = u["text"].split()
        step = (u["end"] - u["start"]) / max(1, len(words))
        tokens += [(index, u["start"] + step * k, _norm(word)) for k, word in enumerate(words)]
    blocks = difflib.SequenceMatcher(
        a=[t[2] for t in tokens], b=[_norm(str(w["word"])) for w in whisper], autojunk=False
    ).get_matching_blocks()
    gaps: dict[int, list[float]] = {}
    for block in blocks:
        if block.size < ANCHOR_RUN:
            continue
        for k in range(block.size):
            index, at, _word = tokens[block.a + k]
            gaps.setdefault(index, []).append(float(whisper[block.b + k]["start"]) - at)
    shifts: dict[int, float] = {}
    for index, found in gaps.items():
        found.sort()
        if len(found) >= 2:
            shifts[index] = found[len(found) // 2]
    out, moved = [], 0
    for index, u in enumerate(utterances):
        words = u["text"].split()
        if _heard_near(words, whisper, u["start"], u["end"]) >= PLACED_SHARE * len(words):
            out.append(u)
            continue
        shift = shifts.get(index)
        if shift is None and shifts:
            shift = shifts[min(shifts, key=lambda k: abs(k - index))]
        if shift is None or abs(shift) < SHIFT_MIN_SEC:
            out.append(u)
            continue
        out.append({**u, "start": max(0.0, u["start"] + shift), "end": max(0.1, u["end"] + shift)})
        moved += 1
    out.sort(key=lambda u: u["start"])
    return out, moved


def fit_times(utterances: list[dict], whisper: list[dict], duration: float) -> tuple[list[dict], dict]:
    """Время реплик → на слова whisper. Ответ: (реплики, {minutes, moved, dropped})."""
    fixed, minutes = _undo_minute_notation(utterances, whisper, duration)
    fixed, moved = _anchor_times(fixed, whisper)
    inside, dropped = [], 0
    for u in fixed:
        if u["start"] >= duration:
            dropped += 1
            continue
        inside.append({**u, "end": min(u["end"], duration)} if u["end"] > duration else u)
    return inside, {"minutes": minutes, "moved": moved, "dropped": dropped}


def plan_windows(words: list[dict], duration: float, limit: float = WINDOW_SEC, overlap: float = WINDOW_OVERLAP) -> list[dict]:
    """Окна звука: ``{start, end, keep_from, keep_to}``. Граница — самая длинная пауза последней
    четверти окна, чтобы не резать фразу; реплика принадлежит окну, где лежит её середина."""
    if duration <= limit:
        return [{"start": 0.0, "end": duration, "keep_from": 0.0, "keep_to": duration}]
    timed = sorted((float(w["start"]), float(w["end"])) for w in words)
    out: list[dict] = []
    begin = 0.0
    while duration - begin > limit:
        target = begin + limit - overlap / 2
        floor = target - (limit - overlap) * 0.25
        edge, best = target, -1.0
        previous = floor
        for a, b in timed:
            if b <= floor:
                continue
            if a >= target:
                break
            if a - previous > best:
                edge, best = (previous + a) / 2, a - previous
            previous = max(previous, b)
        if target - previous > best:
            edge = (previous + target) / 2
        out.append({"start": max(0.0, begin - overlap / 2), "end": edge + overlap / 2, "keep_from": begin, "keep_to": edge})
        begin = edge
    out.append({"start": max(0.0, begin - overlap / 2), "end": duration, "keep_from": begin, "keep_to": duration})
    return out


class _LetterBook:
    """Единые буквы голосов между окнами. В каждом окне модель раздаёт A, B, C заново: сверяем отпечатки
    голосов (app/voice_bank.py), а где отпечатка нет — одинаковые слова на перекрытии. Буквы первого окна
    остаются как есть: A — кого модель назвала главным голосом."""

    def __init__(self, voiceprints: Any = None) -> None:
        self.voiceprints = voiceprints
        self.vectors: dict[str, Any] = {}
        self.words: list[dict] = []
        self.unsure = 0

    def _votes(self, words: list[dict]) -> dict[str, str]:
        votes: Counter = Counter()
        for w in words:
            for old in self.words:
                if _norm(str(w["word"])) == _norm(str(old["word"])) and abs(float(w["start"]) - float(old["start"])) <= 0.5:
                    votes[(w["speaker"], old["speaker"])] += 1
                    break
        mapping: dict[str, str] = {}
        for (new, old), count in votes.most_common():
            if count >= 2 and new not in mapping and old not in mapping.values():
                mapping[new] = old
        return mapping

    def _prints(self, words: list[dict]) -> dict[str, Any]:
        if self.voiceprints is None:
            return {}
        try:
            return self.voiceprints(words) or {}
        except Exception:  # noqa: BLE001 - нет модели или исходника — остаются перекрытие и порядок букв
            self.voiceprints = None
            return {}

    def _same_voice(self, letter: str, vector: Any, taken: set[str]) -> str | None:
        import numpy as np

        scored = sorted(((float(np.dot(vector, print_)), name) for name, print_ in self.vectors.items() if name not in taken),
                        key=lambda x: -x[0])
        if not scored:
            return None
        score, name = scored[0]
        margin = score - scored[1][0] if len(scored) > 1 else score
        if score >= SAME_VOICE_SCORE or (name == letter and score >= SAME_LETTER_SCORE and margin >= SAME_LETTER_MARGIN):
            return name
        return None

    def assign(self, words: list[dict]) -> dict[str, str]:
        """Буквы окна → общие буквы; ``words`` — слова окна со временем клипа."""
        prints = self._prints(words)
        votes = self._votes(words) if self.words else {}
        known = set(self.vectors) | {w["speaker"] for w in self.words}
        mapping: dict[str, str] = {}
        for letter, _count in Counter(w["speaker"] for w in words).most_common():
            taken = set(mapping.values())
            vector = prints.get(letter)
            same = self._same_voice(letter, vector, taken) if vector is not None else None
            if same is None and vector is None and votes.get(letter) not in taken | {None}:
                same = votes[letter]
            if same is None and vector is None and letter in known and letter not in taken:
                self.unsure += 1
                same = letter
            if same is None:
                free = [x for x in LETTERS if x not in taken and x not in known]
                if free:
                    same = letter if letter in free else free[0]
                else:
                    self.unsure += 1
                    spare = [x for x in LETTERS if x not in taken]
                    same = spare[-1] if spare else LETTERS[-1]
            mapping[letter] = same
        for letter, name in mapping.items():
            vector = prints.get(letter)
            if vector is not None and name not in self.vectors:
                self.vectors[name] = vector
        self.words += [{**w, "speaker": mapping[w["speaker"]]} for w in words]
        return mapping


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
    """Строки субтитров из слов: у каждого голоса свои строки. Новая строка — конец предложения
    (. ! ? …), пауза больше LINE_PAUSE, длина и реплика другого голоса между словами; реплики разных
    людей и разные фразы не попадают в одну строку (агент-монтажёр: «ДТП. Бывает. Ну,» одной строкой).
    Когда голоса говорят одновременно, их слова идут вперемешку по времени, но фразу это не рвёт."""
    ordered = sorted(words, key=lambda w: w["start"])
    voices: dict[Any, list[dict]] = {}
    for w in ordered:
        voices.setdefault(w.get("speaker"), []).append(w)
    lines: list[dict] = []
    for voice, own in voices.items():
        others = [w for w in ordered if w.get("speaker") != voice]
        starts = [float(w["start"]) for w in others]
        spoken: list[dict] = []
        cur: list[dict] = []
        for w in own:
            if cur and (
                _SENTENCE_END.search(str(cur[-1]["word"]).strip())
                or float(w["start"]) - float(cur[-1]["end"]) > LINE_PAUSE
                or len(cur) >= LINE_MAX_WORDS
                or _cut_in(others, starts, float(cur[-1]["end"]), float(w["start"]))
            ):
                spoken.append(_line(cur))
                cur = []
            cur.append(w)
        if cur:
            spoken.append(_line(cur))
        # Строки одного голоса не перекрываются (редактор строк этого не любит).
        for one, two in zip(spoken, spoken[1:]):
            if one["end"] > two["start"]:
                one["end"] = two["start"]
        lines += spoken
    lines.sort(key=lambda l: (l["start"], l["end"]))
    return [l for l in lines if l["end"] > l["start"]]


def _line(words: list[dict]) -> dict:
    return {"start": round(words[0]["start"], 3), "end": round(words[-1]["end"], 3),
            "text": " ".join(w["word"] for w in words), "speaker": words[0].get("speaker") or "A"}


def _cut_in(others: list[dict], starts: list[float], left: float, right: float) -> bool:
    """Другой голос сказал слово целиком между ``left`` и ``right``: это другая реплика, не наложение."""
    k = bisect_left(starts, left)
    while k < len(others) and starts[k] < right:
        if float(others[k]["end"]) <= right:
            return True
        k += 1
    return False


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
    from app import modules

    modules.require("voices", store)
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
    windows = plan_windows(draft, _clip_seconds(store, clip_plan_id, draft))
    book = _LetterBook(_voiceprints(store, clip_plan_id) if len(windows) > 1 else None)
    speakers: dict = {}
    utterances: list[dict] = []
    fit = {"minutes": False, "moved": 0, "dropped": 0}
    for window in windows:
        start = window["start"]
        here = [w for w in draft if start <= w["start"] < window["end"]]
        local = [{**w, "start": w["start"] - start, "end": w["end"] - start} for w in here]
        piece = audio if len(windows) == 1 else _audio_piece(audio, start, window["end"])
        about, heard = _listen(client, piece, local)
        heard, facts = fit_times(heard, local, window["end"] - start)
        fit = {"minutes": fit["minutes"] or facts["minutes"], "moved": fit["moved"] + facts["moved"],
               "dropped": fit["dropped"] + facts["dropped"]}
        heard = [{**u, "start": u["start"] + start, "end": u["end"] + start} for u in heard]
        if len(windows) > 1 and heard:
            mapping = book.assign(merge_voices(here, heard))
            heard = [{**u, "s": mapping.get(u["s"], u["s"])} for u in heard]
            about = {mapping[k]: v for k, v in about.items() if k in mapping}
        for letter, text in about.items():
            speakers.setdefault(letter, text)
        last = window is windows[-1]
        utterances += [u for u in heard if window["keep_from"] <= (u["start"] + u["end"]) / 2
                       and (last or (u["start"] + u["end"]) / 2 < window["keep_to"])]
    utterances.sort(key=lambda u: u["start"])
    said = sum(len(u["text"].split()) for u in utterances)
    if not utterances or said < MIN_COVERAGE * len(draft):
        return {"speakers": speakers, "counts": _counts(words), "changed": retranscribed, "added": 0,
                "retranscribed": retranscribed,
                "warning": f"модель расслышала {said} слов из {len(draft)} — голоса не разбираю"}
    matched = _heard_total(utterances, draft)
    if matched < MIN_HEARD * said:
        return {"speakers": speakers, "counts": _counts(words), "changed": retranscribed, "added": 0,
                "retranscribed": retranscribed,
                "warning": f"из {said} слов модели со словами whisper рядом совпало {matched}: "
                           "время не сходится — голоса не разбираю"}
    merged = merge_voices(draft, utterances)
    if len({w["speaker"] for w in merged}) <= 1:
        for w in merged:
            w.pop("speaker", None)
        speakers = {k: v for k, v in speakers.items() if k == "A"}
    lines = lines_from_words(merged)
    # Слова, которые расслышал только Gemini, стоят по его времени — начало строки сверяем с голосом.
    try:
        from app.speech_activity import plan_speech_spans, snap_starts

        snap_starts(lines, merged, plan_speech_spans(store, clip_plan_id))
    except Exception:  # noqa: BLE001 - нет звука для сверки — время как разобрал Gemini, lint_clip покажет
        pass
    fresh = {**payload, "words": merged, "lines": lines, "speakers": speakers, "edited": True}
    fresh["speakers_key"] = _words_key(merged)
    store.set_clip_plan_subtitles(clip_plan_id, fresh)
    out = {"speakers": speakers, "counts": _counts(merged), "changed": True,
           "added": max(0, len(merged) - len(draft)), "retranscribed": retranscribed, "windows": len(windows)}
    if fit["minutes"] or fit["moved"] or fit["dropped"]:
        out["retimed"] = fit
    if book.unsure:
        out["warning"] = f"буквы голосов между окнами сведены без уверенности: {book.unsure} — проверь identify_voices"
    return out


def _listen(client: Any, audio: bytes, words: list[dict]) -> tuple[dict, list[dict]]:
    """Один запрос к модели: звук + черновые слова. Время в ответе — от начала этого звука."""
    listing = "\n".join(f"[{w['start']:.1f}] {w['word']}" for w in words)
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
    return parse_utterances(text)


def _clip_seconds(store: Any, clip_plan_id: int, words: list[dict]) -> float:
    """Длина звука клипа (куски подряд) — шкала слов whisper."""
    last = max((float(w["end"]) for w in words), default=0.0)
    getter = getattr(store, "get_clip_plan", None)
    plan = getter(clip_plan_id) if getter else None
    pieces = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in (plan or {}).get("segments") or [])
    return max(pieces, last)


def _voiceprints(store: Any, clip_plan_id: int) -> Any:
    """Функция «слова окна → отпечаток каждой буквы». Звук и модель поднимаем при первом вызове."""
    state: dict[str, Any] = {}

    def prints(words: list[dict]) -> dict[str, Any]:
        from app.speech_activity import plan_pcm
        from app.voice_bank import MIN_IDENTIFY_SEC, RATE, _cut, _voice_spans, embedder

        if not state:
            state.update(pcm=plan_pcm(store, clip_plan_id), embed=embedder())
        out = {}
        for letter, spans in _voice_spans(words).items():
            chunk = _cut(state["pcm"], spans)
            if len(chunk) / RATE >= MIN_IDENTIFY_SEC:
                out[letter] = state["embed"](chunk)
        return out

    return prints


def _audio_piece(audio: bytes, start: float, end: float) -> bytes:
    """Кусок mp3 клипа без перекодирования."""
    from app.render import _run_ffmpeg

    tag = uuid4().hex
    source, out = settings.tmp_dir / f"speakers-{tag}-all.mp3", settings.tmp_dir / f"speakers-{tag}-part.mp3"
    try:
        source.write_bytes(audio)
        _run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source),
                     "-c", "copy", str(out)], timeout=120)
        return out.read_bytes()
    finally:
        source.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


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
        raise ValueError("видео недоступно")
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    out = settings.tmp_dir / f"speakers-{uuid4().hex}.mp3"
    try:
        _run_ffmpeg(build_clip_audio_args(source_path, pieces, out, source_mix(source)), timeout=300)
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
