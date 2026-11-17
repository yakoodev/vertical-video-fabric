"""Страницы субтитров по фразам, а не по счёту слов.

Пилот 17.11: «меня накажут, если я здесь так | и буду их булить» — страница по числу слов рвала фразу
посреди «так и буду», конец страницы висел на союзе, а короткая страница мелькала быстрее, чем её
прочтёшь. Теперь фраза (между паузами > 0.6–0.75 с и концом предложения — это режет вызывающий)
делится на страницы так, чтобы:

* разрыв шёл по запятой, точке с запятой, двоеточию, тире или паузе ≥ ``PAUSE_HINT_SEC`` (150 мс);
* страница не кончалась предлогом, союзом, частицей («в, и, так, но, что, как, если…») и не
  начиналась с «же, ли, бы»;
* страница из 2+ слов была на экране не меньше ``MIN_PAGE_SEC`` (с задержкой после последнего слова),
  из одного — не меньше ``MIN_WORD_SEC``;
* страницы были примерно поровну (4 слова при 3 на страницу — 2 + 2, а не 3 + 1), одно слово — только
  если иначе никак (или оно само кончается запятой).

Выбор — динамическим программированием по «цене» разбиения (``split_phrase``). Тот же алгоритм с теми
же словарями — во frontend/src/motion/layers/phrasing.ts (слой captions моушна); обе стороны
сверяются с фикстурой tests/fixtures/phrase_pages.json. Здесь — ASS (стили субтитров libass).
"""

from __future__ import annotations

import math
import re
from typing import Callable, Sequence

PAUSE_HINT_SEC = 0.15
MIN_PAGE_SEC = 1.0
MIN_WORD_SEC = 0.8  # страница из одного слова — хотя бы столько, иначе мелькает

PAGE_COST = 1.0
BALANCE_COST = 1.2
ORPHAN_COST = 1.2
ORPHAN_PUNCT_COST = 0.4
SHORT_COST = 2.0
PUNCT_BONUS = 1.5
PAUSE_BONUS_MIN, PAUSE_BONUS_MAX = 0.4, 0.9
FUNCTION_COST = 3.0
WEAK_COST = 0.6
ATTACH_COST = 2.0

# Страница не кончается этим словом: предлоги, союзы, частицы — они смотрят на следующее слово.
FUNCTION_WORDS = frozenset("""
в во на с со к ко о об обо от ото до по за из изо у без безо для при про через над надо под подо перед пред
между сквозь и а но или либо что чтобы чтоб как так если когда пока хотя будто словно ни не то ну
a an the and or but of to in on at for with from by if so as than
""".split())
# Лучше не кончать (местоимение-подлежащее, указательное перед своим словом), но можно.
WEAK_WORDS = frozenset("""
я ты он она оно мы вы они мой моя моё мое мои твой твоя твои наш наша наши ваш ваша ваши этот эта эти тот та те
вот очень самый самая самое такой такая такое такие i you we they my your our their this these
""".split())
# Не начинают страницу: держатся за слово перед собой.
ATTACH_WORDS = frozenset("же ли бы б ль ж".split())

_EDGE = re.compile(r"^[\s.,!?;:…«»\"“”„()\[\]—–-]+|[\s.,!?;:…«»\"“”„()\[\]—–-]+$")
_CLOSERS = "\"'»”)]"
_PUNCT_END = (",", ";", ":", "—", "–", "-")


def bare(word: str) -> str:
    """Слово для словарей: без знаков по краям, строчными, ё → е."""
    return _EDGE.sub("", str(word or "")).lower().replace("ё", "е")


def ends_with_pause_punct(word: str) -> bool:
    """Запятая, точка с запятой, двоеточие или тире в конце слова (кавычки и скобки после — не в счёт)."""
    text = str(word or "").rstrip().rstrip(_CLOSERS)
    return text.endswith(_PUNCT_END)


_FUNCTION = frozenset(w.replace("ё", "е") for w in FUNCTION_WORDS)
_WEAK = frozenset(w.replace("ё", "е") for w in WEAK_WORDS)


def break_cost(text: str, next_text: str, gap: float) -> float:
    """Цена разрыва страницы после слова ``text`` (перед ``next_text``, пауза ``gap`` с): меньше — лучше."""
    cost = 0.0
    if ends_with_pause_punct(text):
        cost -= PUNCT_BONUS
    if gap >= PAUSE_HINT_SEC:
        cost -= min(PAUSE_BONUS_MAX, PAUSE_BONUS_MIN + (gap - PAUSE_HINT_SEC) * 1.5)
    word = bare(text)
    if word in _FUNCTION:
        cost += FUNCTION_COST
    elif word in _WEAK:
        cost += WEAK_COST
    if bare(next_text) in ATTACH_WORDS:
        cost += ATTACH_COST
    return cost


def split_phrase(
    texts: Sequence[str],
    starts: Sequence[float],
    ends: Sequence[float],
    cap: int,
    fits: Callable[[int, int], bool] | None = None,
    hold: float = 0.0,
) -> list[list[int]]:
    """Разбить фразу (слова ``texts`` с временами) на страницы ≤ ``cap`` слов; ``fits(i, j)`` — слова
    ``i..j-1`` помещаются в строки страницы (одно слово — всегда можно). Вернуть номера слов по страницам."""
    n = len(texts)
    if n == 0:
        return []
    cap = max(1, int(cap))
    count = math.ceil(n / cap)
    ideal = n / count
    best = [math.inf] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    breaks = [break_cost(texts[k], texts[k + 1], float(starts[k + 1]) - float(ends[k])) for k in range(n - 1)]
    for j in range(1, n + 1):
        for i in range(j - 1, max(-1, j - cap - 1), -1):
            size = j - i
            if size > 1 and fits is not None and not fits(i, j):
                break  # больше слов тем более не влезет
            if best[i] == math.inf:
                continue
            cost = best[i] + PAGE_COST
            dev = (size - ideal) / ideal
            cost += BALANCE_COST * dev * dev * (1.0 if dev > 0 else 0.6)
            if size == 1 and n > 1:
                cost += ORPHAN_PUNCT_COST if ends_with_pause_punct(texts[i]) else ORPHAN_COST
            if n > 1:
                shown = float(ends[j - 1]) - float(starts[i]) + hold
                least = MIN_PAGE_SEC if size >= 2 else MIN_WORD_SEC
                if shown < least:
                    cost += SHORT_COST * (least - shown)
            if j < n:
                cost += breaks[j - 1]
            if cost < best[j] - 1e-9:
                best[j] = cost
                prev[j] = i
    pages: list[list[int]] = []
    j = n
    while j > 0:
        i = prev[j]
        pages.append(list(range(i, j)))
        j = i
    pages.reverse()
    return pages
