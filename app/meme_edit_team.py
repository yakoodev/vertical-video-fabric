"""Мем-эдит: реплики тиммейтов без шума (чистая функция + словарь слов).

Тиммейты слышны только на дорожке игры — вперемешку с выстрелами, шагами и голосом диктора. Whisper там часто
«слышит» речь в шуме и выдаёт мусор вроде «В ЛОВЫЙ ГОЛОВ А», который потом встаёт голубыми субтитрами и в смысл
мемов. Общий фильтр распознавания (app/subtitles/whisper_local.py) режет только совсем неуверенное (avg_logprob
< −1), поэтому для тиммейтов строже — по репликам (``lines_from_words``), первое сработавшее правило — причина:

* **речь игрока** — реплика больше чем наполовину идёт поверх слов микрофона и это либо те же слова (эхо
  микрофона в игре, распознанное чуть иначе, — точные совпадения раньше убирает app/meme_edit_inputs.drop_bleed),
  либо слова неуверенные (каша двух голосов). Уверенная реплика поверх игрока остаётся: на боях пилотов это почти
  всегда настоящий тиммейт («С этой стороны» поверх «Невозможно… со спины сквад!»);
* **неуверенно** — средний логарифм вероятности сегмента ниже MIN_LOGPROB (сегмент Whisper — до 30 с, поэтому
  правило грубое: целое окно мусора под перестрелкой);
* **не речь** — декодер считает отрезок тишиной/шумом (no_speech_prob > NOISY_NO_SPEECH) и не уверен в словах;
* **повторы** — сжатие текста выше MAX_COMPRESSION («на-на-на-на», одно слово кругом);
* **слова неуверенно** — средняя вероятность слов ниже MIN_WORD_PROB (короткая реплика — ниже MIN_SHORT_PROB);
* **обрывок** — одно-два коротких слова без смысла (не «нок», «да», «есть»…);
* **незнакомые слова** — больше половины значимых слов нет ни в словаре русского/английского (частотные списки
  разговорной речи, скачиваются в ``DATA_DIR/wordlists`` при первом вызове), ни в игровом сленге (GAME_SLANG), ни
  в мате (мат — настоящая речь).

Реальные реплики («Красавцы!», «Банга, я пушу», «На мне все») проходят. Слов без уверенности (старый кэш,
подмена в тестах) правила уверенности не трогают. Словаря нет (нет сети) — правило «незнакомые слова» пропускается.

    filter_team(team_words, owner_words) -> (оставленные слова, [{start, end, text, why}])
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

OVERLAP_OWNER = 0.5
ECHO_SIMILAR = 0.6       # доля букв реплики, найденная в словах игрока поверх неё — эхо микрофона
CROSSTALK_PROB = 0.7     # поверх игрока и слова неуверенные — каша голосов
MIN_LOGPROB = -0.6
NOISY_NO_SPEECH = 0.5
NOISY_LOGPROB = -0.45
MAX_COMPRESSION = 2.3
MIN_WORD_PROB = 0.5
MIN_SHORT_PROB = 0.6     # реплика из одного-двух слов: мусор чаще всего такой
UNKNOWN_SHARE = 0.5
# Короткие реплики, которые настоящие (колл в бою).
SHORT_OK = frozenset("""да нет нок нокнул есть изи гг нт ап хп лоу го иду стой жди тут там где кто я мне вот
он она они ок окей найс сзади слева справа сверху снизу щит хил дрон все всё лут бей""".split())
# Игровой сленг и имена легенд Apex (основы, по началу слова): в частотных словарях их нет.
GAME_SLANG = ("нок", "ренок", "сквад", "ульт", "тактик", "хил", "батар", "батк", "шилд", "лут", "пуш", "пушн", "рев",
              "респ", "банг", "бангалор", "лайфлайн", "ватсон", "ваттсон", "рейф", "рэйф", "бладх", "бх", "крипт",
              "хорайз", "октейн", "патфайнд", "пасфайнд", "вальк", "сир", "кауст", "гибр", "джибр", "мираж", "лоба",
              "рэмп", "рамп", "кэтал", "катал", "эш", "баллист", "кондуит", "альтер", "ньюкасл", "краб", "флэт",
              "флет", "спитф", "диво", "хаес", "хаеш", "хемлок", "волт", "вольт", "пикс", "пика", "тэп", "тэпн",
              "дезб", "дроп", "ринг", "кольц", "найс", "изи", "тиммейт", "тимм", "дрон", "снайп", "дробаш", "шотган",
              "смг", "ар", "мозамб", "пиксел", "рэмпейдж", "сентин", "лонгбоу", "триптейк", "бокер", "пик", "кемп",
              "хайграунд", "реск", "реснут", "рес", "добив", "фраг", "килл", "килфид", "дмг", "дэмедж", "демедж",
              "лобби", "дуо", "соло", "трио", "лег", "ренк", "ранк", "ранкед", "пабл", "кьюшк", "ку", "ешк", "лаг", "ласт",
              "голд", "фиол", "синьк", "мобилк", "зиплайн", "джамп")
PREFIXES = ("кибер", "супер", "пере", "недо", "под", "при", "про", "раз", "рас", "без", "бес", "от", "по", "на", "за",
            "вы", "до", "об", "из", "ис", "у", "с", "в")
WORDLIST_URLS = {
    "ru": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ru/ru_50k.txt",
    "en": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt",
}
WORDLIST_MIN_BYTES = 200_000
RETRY_SEC = 3600.0

_WORD = re.compile(r"[a-zа-яё0-9]+(?:-[a-zа-яё0-9]+)*")
_lock = threading.Lock()
_lexicon: "Lexicon | None" = None
_failed_at = 0.0


class Lexicon:
    """Слова разговорной речи (частотные списки OpenSubtitles, Hermit Dave, CC BY-SA 4.0) + их основы без 1–3
    последних букв (формы слова, которых нет в списке: «намудрили» ↔ «намудрил»)."""

    def __init__(self, words: set[str]):
        self.words = words
        self.stems = {w[:-k] for w in words for k in (1, 2, 3) if len(w) - k >= 4}

    def knows(self, word: str) -> bool:
        if self._base(word):
            return True
        # приставка («кибер-спортивное», «на-мудрили», «под-бегает») — слово без неё
        return any(word.startswith(p) and len(word) - len(p) >= 5 and self._base(word[len(p):]) for p in PREFIXES)

    def _base(self, word: str) -> bool:
        if word in self.words:
            return True
        return any(len(word) - k >= 4 and (word[:-k] in self.stems or word[:-k] in self.words) for k in (1, 2, 3))


def wordlist_dir() -> Path:
    from app.settings import settings

    return settings.data_dir / "wordlists"


def _read_list(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.split(" ", 1)[0].strip().lower().replace("ё", "е")
        if word:
            out.add(word)
    return out


def lexicon() -> Lexicon | None:
    """Словарь (скачать при первом вызове, ≈ 1.3 МБ) или None — нет и скачать не вышло (раз в час пробуем снова)."""
    global _lexicon, _failed_at
    with _lock:
        if _lexicon is not None:
            return _lexicon
        if time.monotonic() - _failed_at < RETRY_SEC and _failed_at:
            return None
        folder = wordlist_dir()
        words: set[str] = set()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for lang, url in WORDLIST_URLS.items():
                path = folder / f"{lang}_50k.txt"
                if not path.is_file() or path.stat().st_size < WORDLIST_MIN_BYTES:
                    if os.environ.get("WORDLIST_DOWNLOAD", "1") == "0":
                        raise OSError("скачивание словаря выключено (WORDLIST_DOWNLOAD=0)")
                    tmp = path.with_suffix(".part")
                    with urllib.request.urlopen(url, timeout=20) as resp, tmp.open("wb") as fh:  # noqa: S310
                        fh.write(resp.read())
                    if tmp.stat().st_size < WORDLIST_MIN_BYTES:
                        raise OSError(f"{url}: слишком маленький файл")
                    tmp.replace(path)
                words |= _read_list(path)
        except Exception as exc:  # noqa: BLE001 - без словаря — без этого правила
            _failed_at = time.monotonic()
            _log.warning("meme_edit_team: словарь слов не загрузился (%s) — правило «незнакомые слова» пропущено",
                         str(exc)[:160])
            return None
        _lexicon = Lexicon(words)
        return _lexicon


def _norm(word: Any) -> list[str]:
    return [w.replace("ё", "е") for w in _WORD.findall(str(word or "").lower())]


def _known(word: str, lex: Lexicon) -> bool:
    from app.profanity import classify_token

    if word.isdigit() or len(word) <= 2 or word in SHORT_OK:
        return True
    if any(word.startswith(s) for s in GAME_SLANG if len(s) >= 3) or word in GAME_SLANG:
        return True
    if classify_token(word, ("mat", "rude", "soft")):
        return True
    return all(lex.knows(part) or part.isdigit() or len(part) <= 2 for part in word.split("-"))


def _overlap(a: float, b: float, spans: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(b, z) - max(a, s)) for s, z in spans)


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    mine = [t for t in _norm(a) if len(t) >= 3] or _norm(a)
    theirs = _norm(b)
    if not mine or not theirs:
        return 0.0
    # какая доля слов реплики есть у игрока (с ошибками распознавания: «стёр» ~ «стер», «сквака» ~ «сквада»)
    hits = sum(1 for t in mine if any(SequenceMatcher(None, t, o).ratio() >= 0.75 for o in theirs))
    return hits / len(mine)


def judge(line: dict, owner: list[dict], lex: Lexicon | None) -> str | None:
    """Почему реплика тиммейта — шум (причина) или None — настоящая."""
    words = line["words"]
    a, b = float(line["start"]), float(line["end"])
    dur = max(0.05, b - a)
    ps = [x for x in (_num(w.get("p")) for w in words) if x is not None]
    if owner and _overlap(a, b, [(float(w["start"]), float(w["end"])) for w in owner]) >= OVERLAP_OWNER * dur:
        said = " ".join(str(w["word"]) for w in owner if float(w["start"]) < b + 0.3 and float(w["end"]) > a - 0.3)
        if _similar(line["text"], said) >= ECHO_SIMILAR:
            return "речь игрока: эхо микрофона"
        if ps and sum(ps) / len(ps) < CROSSTALK_PROB:
            return "речь игрока: каша голосов"
    lps = [x for x in (_num(w.get("lp")) for w in words) if x is not None]
    nss = [x for x in (_num(w.get("ns")) for w in words) if x is not None]
    crs = [x for x in (_num(w.get("cr")) for w in words) if x is not None]
    if lps and min(lps) < MIN_LOGPROB:
        return f"неуверенно: avg_logprob {min(lps):.2f}"
    if nss and lps and max(nss) > NOISY_NO_SPEECH and min(lps) < NOISY_LOGPROB:
        return f"не речь: no_speech {max(nss):.2f}"
    if crs and max(crs) > MAX_COMPRESSION:
        return f"повторы: сжатие {max(crs):.1f}"
    tokens = [t for w in words for t in _norm(w.get("word"))]
    if ps:
        mean_p = sum(ps) / len(ps)
        if mean_p < (MIN_SHORT_PROB if len(tokens) <= 2 else MIN_WORD_PROB):
            return f"слова неуверенно: {mean_p:.2f}"
    letters = sum(len(t) for t in tokens if not t.isdigit())
    if not tokens or (len(tokens) <= 2 and letters <= 4 and not any(t in SHORT_OK or t.isdigit() for t in tokens)):
        return "обрывок"
    if lex is not None:
        content = [t for t in tokens if len(t) >= 3 and not t.isdigit()]
        unknown = [t for t in content if not _known(t, lex)]
        # одно значимое слово — шум, только если реплика из одного-двух слов («Деноса»), иначе — половина и больше
        if unknown and (len(content) >= 2 and len(unknown) >= UNKNOWN_SHARE * len(content)
                        or len(content) == 1 and len(tokens) <= 2):
            return "незнакомые слова: " + ", ".join(unknown[:4])
    return None


def filter_team(team: list[dict], owner: list[dict], *, lex: Any = "auto") -> tuple[list[dict], list[dict]]:
    """Слова тиммейтов без реплик-шума. ``lex`` — словарь (``"auto"`` — ``lexicon()``, None — без правила
    «незнакомые слова»). Ответ — (оставленные слова по времени, выброшенные реплики ``{start, end, text, why}``)."""
    from app.meme_edit_fights import lines_from_words

    if lex == "auto":
        lex = lexicon()
    owner = sorted(owner or [], key=lambda w: float(w["start"]))
    kept: list[dict] = []
    dropped: list[dict] = []
    for line in lines_from_words(team):
        why = judge(line, owner, lex)
        if why:
            dropped.append({"start": line["start"], "end": line["end"], "text": line["text"], "why": why})
        else:
            kept += line["words"]
    return sorted(kept, key=lambda w: float(w["start"])), dropped
