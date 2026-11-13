"""Распознавание озвучки: что на самом деле звучит в файле голоса (Whisper, слова с временем).

Нужно трём местам: защите синтеза Gemini TTS (первые секунды — тот ли текст, не зачитано ли указание),
``add_voiceover`` (субтитры по настоящим словам) и анализатору ``check_voiceover`` (услышано = написано).
Результат по файлу кэшируется (путь + размер + время правки): повторная проверка клипа не гоняет Whisper заново.

Тесты подменяют ``TRANSCRIBE`` (функция ``path -> [{word, start, end}]``).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from app.settings import settings

_log = logging.getLogger(__name__)

# Подмена в тестах: path -> [{word, start, end}]. None — настоящий Whisper (app/explainer_gen.transcribe_words).
TRANSCRIBE: Callable[[Path], list[dict]] | None = None
HEAD_SEC = 2.5  # защита синтеза: сколько первых секунд слушать
HEAD_WORDS = 3  # сколько первых распознанных слов сверять с началом текста
WORD_SIMILAR = 0.72  # слово «то же» (Витубика ↔ Витупика, FREE10 ↔ Free10)
_MEM: dict[str, list[dict]] = {}
_LOCK = threading.Lock()
_MEM_MAX = 256


def norm(word: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


# Whisper пишет услышанное по-своему: «лайв-два-дэ» → «Live 2D», «фри десять» → «FREE10», «десять процентов» → «10%».
# Чтобы это не было «ошибкой», обе стороны сравнения разворачиваются в «звучание»: цифры — словами, латиница —
# кириллицей по произношению, одиночная латинская буква — её русским названием («D» → «дэ»).
_UNITS = ("ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать "
          "четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать").split()
_TENS = {2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят", 6: "шестьдесят", 7: "семьдесят", 8: "восемьдесят",
         9: "девяносто"}
_LETTER = {"a": "а", "b": "бэ", "c": "цэ", "d": "дэ", "e": "е", "f": "эф", "g": "гэ", "h": "аш", "i": "ай", "j": "джей",
           "k": "кей", "l": "эль", "m": "эм", "n": "эн", "o": "о", "p": "пэ", "q": "кью", "r": "эр", "s": "эс", "t": "тэ",
           "u": "ю", "v": "ви", "w": "вэ", "x": "икс", "y": "игрек", "z": "зэт"}
_DIGRAPHS = (("sch", "ш"), ("ee", "и"), ("oo", "у"), ("ea", "и"), ("th", "т"), ("sh", "ш"), ("ch", "ч"), ("ph", "ф"),
             ("ck", "к"), ("qu", "кв"), ("kh", "х"), ("zh", "ж"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"))
_LATIN = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж", "k": "к",
          "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
          "w": "в", "x": "кс", "y": "и", "z": "з"}


def _number_words(digits: str) -> list[str]:
    n = int(digits)
    if n < 20:
        return [_UNITS[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_UNITS[n % 10]] if n % 10 else [])
    return [digits]


def _latin(run: str) -> list[str]:
    if len(run) == 1:
        return [_LETTER.get(run, run)]
    word = run
    if len(word) > 3 and word.endswith("e") and word[-2] not in "aeiouy":
        word = word[:-1]  # live → лив, store → стор
    for a, b in _DIGRAPHS:
        word = word.replace(a, b)
    word = re.sub(r"c(?=[eiy])", "с", word)
    return ["".join(_LATIN.get(ch, ch) for ch in word)]


def expand(word: Any) -> list[str]:
    """Слово → его «звучание» по кускам: «FREE10» → [фри, десять], «2D» → [два, дэ], «10%» → [десять, процентов]."""
    raw = str(word or "").lower().replace("ё", "е")
    out: list[str] = []
    for run in re.findall(r"[0-9]+|[a-z]+|[а-я]+", raw):
        if run.isdigit():
            out += _number_words(run)
        elif run[0] in "abcdefghijklmnopqrstuvwxyz":
            out += _latin(run)
        else:
            out.append(run)
    if "%" in raw:
        out.append("процентов")
    return out


def tokens(text: str) -> list[str]:
    return [t for w in re.split(r"[\s—–/-]+", str(text or "")) for t in expand(w)]


def heard_tokens(words: list[dict]) -> list[str]:
    return [t for w in words for t in expand(w.get("word"))]


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or (len(a) >= 4 and len(b) >= 4 and (a.startswith(b[:4]) and b.startswith(a[:4]))):
        return True
    # Латиница по-русски гласными врёт («Live» → «лив», сказано «лайв»): тот же остов согласных — то же слово.
    ka, kb = _skeleton(a), _skeleton(b)
    if len(ka) >= 2 and ka == kb and a[0] == b[0]:
        return True
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio() >= WORD_SIMILAR


def _skeleton(word: str) -> str:
    return re.sub(r"[аеёиоуыэюяйьъ]", "", word)


def _cache_dir() -> Path:
    path = settings.runtime_dir / "voice_asr"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(path: Path) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _real_transcribe(path: Path) -> list[dict]:
    from app.explainer_gen import transcribe_words

    return transcribe_words(path)


def transcribe(path: Path | str) -> list[dict]:
    """Слова файла ``[{word, start, end}]`` (секунды файла). Кэш — в памяти и в runtime/voice_asr."""
    path = Path(path)
    fn = TRANSCRIBE or _real_transcribe
    if TRANSCRIBE is not None:  # тесты: без кэша, чтобы подмена видела каждый вызов
        return list(fn(path))
    key = _key(path)
    with _LOCK:
        if key in _MEM:
            return list(_MEM[key])
    disk = _cache_dir() / f"{key}.json"
    words: list[dict] | None = None
    if disk.is_file():
        try:
            words = json.loads(disk.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            words = None
    if words is None:
        words = [{"word": str(w["word"]), "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)}
                 for w in fn(path)]
        try:
            disk.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    with _LOCK:
        if len(_MEM) > _MEM_MAX:
            _MEM.clear()
        _MEM[key] = list(words)
    return list(words)


def head_transcribe(path: Path, seconds: float = HEAD_SEC) -> list[dict]:
    """Слова первых ``seconds`` файла (отрезок — отдельным wav: Whisper по 2.5 с, а не по всей фразе)."""
    if TRANSCRIBE is not None:
        return [w for w in TRANSCRIBE(Path(path)) if float(w["start"]) < seconds]
    head = Path(path).with_name(Path(path).stem + ".head.wav")
    try:
        proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-t",
                               f"{seconds:.2f}", "-ac", "1", "-ar", "16000", str(head)],
                              capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "")[-200:])
        return _real_transcribe(head)
    finally:
        head.unlink(missing_ok=True)


def head_matches(text: str, heard: list[dict]) -> bool:
    """Начало звука — начало текста? Первое распознанное слово должно быть среди первых слов текста.

    Ничего не распознано (тихий вдох, шёпот) — не отказ: проверять нечего, синтез не повторяем."""
    want = tokens(text)[: HEAD_WORDS + 2]
    got = heard_tokens(heard)[:HEAD_WORDS]
    if not want or not got:
        return True
    if any(similar(got[0], t) for t in want[:HEAD_WORDS]):
        return True
    # Whisper склеил/разбил слово: хотя бы два из первых слов звука есть в начале текста.
    return sum(any(similar(g, t) for t in want) for g in got) >= 2


def lead_extra(text: str, heard: list[dict]) -> list[str]:
    """Лишние слова В НАЧАЛЕ звука, которых нет в начале текста (зачитанное указание «говори бодро…»)."""
    want = tokens(text)
    if not want:
        return []
    out: list[str] = []
    for w in heard:
        keys = expand(w.get("word"))
        if not keys:
            continue
        if any(similar(key, t) for key in keys for t in want[:HEAD_WORDS + 1]):
            break
        out.append(str(w.get("word")).strip())
        if len(out) >= 12:
            break
    else:
        return []  # первое слово текста так и не прозвучало — это не «лишнее в начале», а другой текст
    return out


def word_errors(text: str, heard: list[dict]) -> tuple[float, list[str], list[str]]:
    """WER по словам с нечётким равенством: (доля ошибок, пропущенные слова текста, лишние слова звука)."""
    want = tokens(text)
    got = heard_tokens(heard)
    if not want:
        return (0.0 if not got else 1.0), [], got
    n, m = len(want), len(got)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if similar(want[i - 1], got[j - 1]) else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
    # Что пропущено и что лишнее — обратный проход.
    missing, extra = [], []
    i, j = n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (0 if similar(want[i - 1], got[j - 1]) else 1):
            if not similar(want[i - 1], got[j - 1]):
                missing.append(want[i - 1])
                extra.append(got[j - 1])
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            missing.append(want[i - 1])
            i -= 1
        else:
            extra.append(got[j - 1])
            j -= 1
    return round(d[n][m] / n, 3), missing[::-1], extra[::-1]


def missing_tail(text: str, heard: list[dict], tail: int = 2) -> list[str]:
    """Последние слова текста, которых нет в конце распознанного (обрезанный хвост фразы)."""
    want = tokens(text)
    got = heard_tokens(heard)
    if len(want) < 2:
        return []
    last = got[-6:]
    gone = []
    for t in reversed(want):
        if any(similar(t, g) for g in last):
            break
        gone.append(t)
        if len(gone) >= 12:
            break
    return gone[::-1] if len(gone) >= tail or (gone and len(want) <= 4) else []


def heard_share(text: str, heard: list[dict]) -> float:
    """Доля слов текста, которые прозвучали (по «звучанию», как word_errors): 1.0 — всё на месте."""
    want = tokens(text)
    if not want:
        return 0.0
    _wer, missing, _extra = word_errors(text, heard)
    return round(max(0.0, (len(want) - len(missing)) / len(want)), 3)
