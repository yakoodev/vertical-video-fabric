"""Видеомемы по смыслу: реплика игрока против того, что говорит мем, — локальной моделью (app/text_embed.py).

Два сигнала (замеры на выборе монтажёра — tools/meme_semantic_eval.py):

* **фраза** — вектор реплики против вектора того, что сказано в меме (расшифровка, без неё — подпись): «Ой, как я
  всё хорошо сделал» ↔ «Потому что я умный, делаю всё правильно»;
* **смысловое эхо** — лучшая пара значимых слов реплики и мема: «Альцгеймер» ↔ «болезнь» (общих букв нет, слово
  отвечает слову — правило владельца «мем повторяет слово игрока»).

Описание мема и «когда ставить» в подбор к реплике не идут: они написаны про игровые ситуации («после смерти от
читера…») и тянут любую реплику со словом «убил» к одним и тем же мемам. Для поиска (``search_scores``: агент,
страница «Видеомемы») они нужны — там запрос как раз про ситуацию.

Векторы мемов считаются один раз и лежат в кэше ``runtime/embed/memes-<модель>.json`` по ключу «id : поле : хэш
текста»; слова — там же по слову. Мат (и «б***») из текста убирается: это не смысл. Модели нет — всё отдаёт None,
подбор идёт по словам, как раньше.

Сырой косинус у моделей в разной шкале (e5: 0.75–0.9, MiniLM: 0–0.7), поэтому наружу — z-оценка по библиотеке
(``standardize``): 0 — как средний мем, 2 — заметно ближе всех.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app import text_embed as TE

_log = logging.getLogger(__name__)

# Смысловое эхо слов — редкий сильный сигнал («Альцгеймер» ↔ «болезнь» — 4 σ над библиотекой), а слабое эхо — шум
# («убил» ↔ «убивает» у половины мемов): в счёт идёт только часть выше ECHO_FLOOR σ, с весом ECHO_SHARE.
ECHO_SHARE = 2.0
ECHO_FLOOR = 2.5
SAME_WORD = 0.97        # пара слов ближе — то же слово (буквальное эхо считается отдельно, по основе)
MIN_WORD_LEN = 4
_lock = threading.Lock()
_memory: dict[str, dict[str, str]] = {}  # модель → {ключ: base64 float16}
_STOP = frozenset("""это эта этот эти того тому там тут так вот ну да нет ага угу я ты он она оно мы вы они меня тебя
его её ее нас вас их мне тебе ему ей нам вам им все всё всех уже еще ещё как что кто где когда зачем почему если чтобы
только просто вообще сейчас тоже очень было была были будет есть нету надо можно нужно давай давайте потому поэтому
другой другая другого какой какая такой такая одного одной один одна этого этой тогда потом снова опять может вроде
типа короче слушай смотри какой-то какая-то если что чего чё the and you that this what""".split())
_WORD = re.compile(r"[a-zа-яё]+(?:-[a-zа-яё]+)?")


# --------------------------------------------------------------------------- #
# Тексты
# --------------------------------------------------------------------------- #


def clean(text: Any) -> str:
    """Без мата (и «б***» со звёздочками): мат — не смысл, а кодировщик тянет фразу с матом к мемам с матом."""
    from app.profanity import classify_token

    kept = [w for w in str(text or "").split() if "*" not in w and not classify_token(w, ("mat", "rude"))]
    out = re.sub(r"\s+([,.!?…])", r"\1", " ".join(kept))
    return re.sub(r"([,.!?…])(?:\s*[,.!?…])+", r"\1", out).strip(" ,")


def content_words(text: Any) -> list[str]:
    """Значимые слова для эха: ≥ 4 букв, не служебные, не мат; без повторов, по порядку."""
    out: list[str] = []
    for w in _WORD.findall(clean(text).lower()):
        w = w.replace("ё", "е")
        if len(w) >= MIN_WORD_LEN and w not in _STOP and w not in out:
            out.append(w)
    return out


def said_text(meme: dict) -> str:
    """Что говорит мем: расшифровка, без неё — подпись (мем без слов: «Мистер Бин в шоке»)."""
    said = clean(meme.get("transcript"))
    return said or clean(meme.get("title") or meme.get("label")) or str(meme.get("title") or "")


def full_text(meme: dict) -> str:
    """Мем целиком (для поиска): что сказано, подпись, когда ставить, что значит, теги."""
    parts = [said_text(meme), clean(meme.get("title") or meme.get("label")), clean(meme.get("when_to_use")),
             clean(meme.get("description"))]
    tags = meme.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if tags:
        parts.append("Настроение: " + ", ".join(str(t) for t in tags))
    return " ".join(p if p.endswith((".", "!", "?", "…")) else p + "." for p in parts if p)[:1500]


def query_text(line: str, context: list[str] | None = None, event: str = "") -> str:
    """Запрос для поиска: предыдущие реплики, сама реплика, что случилось в игре."""
    parts = [clean(c) for c in (context or [])] + [clean(line)]
    text = " ".join(p for p in parts if p)
    if event:
        text = f"{text} ({event})" if text else event
    return text[:800]


# --------------------------------------------------------------------------- #
# Кэш векторов
# --------------------------------------------------------------------------- #


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _cache_file(model: str) -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "embed"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"memes-{model}.json"


def _load(model: str) -> dict[str, str]:
    if model in _memory:
        return _memory[model]
    data: dict[str, str] = {}
    try:
        raw = json.loads(_cache_file(model).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = {str(k): str(v) for k, v in raw.items()}
    except (OSError, ValueError):
        pass
    _memory[model] = data
    return data


def _save(model: str, data: dict[str, str]) -> None:
    file = _cache_file(model)
    tmp = file.with_suffix(".part")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(file)
    except OSError as exc:
        _log.info("meme_semantic: кэш векторов не записан: %s", exc)


def _pack(vec: Any) -> str:
    import numpy as np

    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode("ascii")


def _unpack(text: str) -> Any:
    import numpy as np

    return np.frombuffer(base64.b64decode(text), dtype=np.float16).astype(np.float32)


def clear_memory() -> None:
    """Забыть векторы в памяти (тесты, смена модели)."""
    with _lock:
        _memory.clear()


def _vectors(entries: list[tuple[str, str]], kind: str) -> Any:
    """Векторы текстов по ключам кэша ``(ключ, текст)``: есть — из кэша, нет — посчитать и записать. None — нет
    модели. Ключ мема с прежним текстом (тот же «id : поле», другой хэш) выбрасывается."""
    import numpy as np

    model = TE.tag()
    cache = _load(model)
    missing = [i for i, (k, _t) in enumerate(entries) if k not in cache]
    if missing:
        fresh = TE.embed([entries[i][1] for i in missing], kind=kind)
        if fresh is None:
            return None
        prefixes = {entries[i][0].rsplit(":", 1)[0] for i in missing if entries[i][0].startswith("m")}
        wanted = {k for k, _t in entries}
        for k in [k for k in cache if k.startswith("m") and k.rsplit(":", 1)[0] in prefixes and k not in wanted]:
            cache.pop(k, None)
        for i, vec in zip(missing, fresh):
            cache[entries[i][0]] = _pack(vec)
        _save(model, cache)
    if not entries:
        return np.zeros((0, 1), dtype=np.float32)
    matrix = np.stack([_unpack(cache[k]) for k, _t in entries])
    return matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)


class Bank:
    """Векторы библиотеки мемов: что сказано (``said``), мем целиком (``full``), значимые слова (``words`` — все
    подряд, ``owner`` — чей это мем). Строится ``bank(memes)``."""

    def __init__(self, memes: list[dict], said: Any, full: Any, words: Any, owner: Any):
        self.memes = memes
        self.said = said
        self.full = full
        self.words = words
        self.owner = owner


def bank(memes: list[dict]) -> Bank | None:
    """Векторы библиотеки (досчитываются и кэшируются) или None — модели нет."""
    import numpy as np

    if not memes:
        return None
    with _lock:
        def mid(m: dict) -> Any:
            return m.get("meme_id", m.get("id"))

        said_t = [said_text(m) for m in memes]
        full_t = [full_text(m) for m in memes]
        said = _vectors([(f"m{mid(m)}:said:{_digest(t)}", t) for m, t in zip(memes, said_t)], "passage")
        if said is None:
            return None
        full = _vectors([(f"m{mid(m)}:full:{_digest(t)}", t) for m, t in zip(memes, full_t)], "passage")
        words_per = [content_words(f"{s} {m.get('title') or ''}") for s, m in zip(said_t, memes)]
        vocab = sorted({w for ws in words_per for w in ws})
        wvec = _vectors([(f"w:{w}", w) for w in vocab], "query")
        if full is None or wvec is None:
            return None
        index = {w: i for i, w in enumerate(vocab)}
        rows, owner = [], []
        for n, ws in enumerate(words_per):
            for w in ws:
                rows.append(index[w])
                owner.append(n)
        words = wvec[rows] if rows else np.zeros((0, said.shape[1]), dtype=np.float32)
        return Bank(memes, said, full, words, np.asarray(owner, dtype=np.int64))


# --------------------------------------------------------------------------- #
# Оценки
# --------------------------------------------------------------------------- #


def standardize(sims: Any) -> Any:
    """Косинусы → z-оценка по библиотеке. Мемов меньше пяти — шкалы нет: отклонение от среднего в долях 0.05
    (типичный разброс косинусов)."""
    import numpy as np

    arr = np.asarray(sims, dtype=np.float32)
    if arr.size == 0:
        return arr
    std = float(arr.std()) if arr.size >= 5 else 0.0
    return (arr - float(arr.mean())) / (std if std > 1e-4 else 0.05)


def _echo(words: list[str], lib: Bank) -> Any:
    """Лучшая пара «слово реплики — слово мема» по каждому мему (мем без значимых слов — средний)."""
    import numpy as np

    count = len(lib.memes)
    if not words or not len(lib.owner):
        return None
    with _lock:
        q = _vectors([(f"w:{w}", w) for w in words], "query")
    if q is None:
        return None
    sims = q @ lib.words.T
    sims[sims >= SAME_WORD] = -1.0  # то же слово — это буквальное эхо, его считает app/meme_edit_memes.echo_words
    pair = sims.max(axis=0)  # по каждому слову мемов
    best = np.full(count, -1.0, dtype=np.float32)
    np.maximum.at(best, lib.owner, pair)
    missing = best < -0.5
    if missing.all():
        return None
    best[missing] = float(best[~missing].mean())
    return best


def line_scores(line: str, lib: Bank | None) -> Any:
    """Смысл реплики против каждого мема библиотеки (по порядку): z(фраза) + ECHO_SHARE·z(эхо слов), снова в
    z-шкале. None — модели нет / в реплике одни служебные слова и мат."""
    if lib is None:
        return None
    text = clean(line)
    if not text:
        return None
    vec = TE.embed([text], kind="query")
    if vec is None:
        return None
    import numpy as np

    total = standardize(TE.similarity(vec[0], lib.said))
    echo = _echo(content_words(text), lib)
    if echo is not None:
        total = total + ECHO_SHARE * np.maximum(0.0, standardize(echo) - ECHO_FLOOR)
    return standardize(total)


def search_scores(query: str, lib: Bank | None) -> Any:
    """Поиск по смыслу (агент, страница): запрос против мема целиком и против того, что он говорит, — лучшее из
    двух, z-шкала. None — модели нет."""
    import numpy as np

    if lib is None or not clean(query):
        return None
    vec = TE.embed([clean(query)], kind="query")
    if vec is None:
        return None
    full = standardize(TE.similarity(vec[0], lib.full))
    said = standardize(TE.similarity(vec[0], lib.said))
    return np.maximum(full, said)
