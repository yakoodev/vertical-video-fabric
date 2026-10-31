"""Мат в словах субтитров: найти, заглушить серединой, записать звёздочками.

Правило владельца для площадок: мат глушится только СЕРЕДИНОЙ слова — зритель понимает,
что сказано, а автосубтитры TikTok/YouTube мат не распознают; в субтитрах — первая буква и
звёздочки («б****»). Агенты делали это руками внешними скриптами по 3–4 круга на клип;
здесь — чистая логика без хранилища (оркестровка, рендер и проверка Whisper — app/censor.py).

Словарь — стемы с допустимыми приставками, а не подстроки: «еб» внутри слова есть в «хлеб»,
«небо», «себя», «вебка», «бля» — в «рубля», «корабля», «употребляю». Поэтому корень ищется
в начале слова или сразу после приставки из списка (для «еб» у согласных приставок — только
с ъ/ь: «съебал», но не «себя»). Корни, которых в обычных словах нет («пизд», «залуп»), —
в любом месте слова.

Группы:

* ``mat`` — настоящий мат (хуй, пизда, ебать, блядь и производные) и обсценная брань той же
  силы (мудак, пидор, гандон, шлюха, залупа). Всегда.
* ``rude`` — «сука» и производные. По умолчанию ВКЛ: филологически не мат, но на стримах это
  самое частое ругательство, фильтры автосубтитров площадок ловят его наравне с матом, а
  ложных срабатываний после исключений («сук» — ветка, «сукно», «суккулент», «сучок») почти нет.
* ``soft`` — блин, бляха, хрен, херня, жопа, говно, дерьмо, срать, дрочить. По умолчанию ВЫКЛ:
  это не мат, площадки за них не режут, а глушение каждого «блин» рвёт живую речь.

Слово, уже записанное звёздочками («б****», «бл*ть»), — тоже мат (группа ``starred``): так
повторный запуск находит то, что сам же заменил, и глушит снова, а не теряет.
"""

from __future__ import annotations

import re
from typing import Any

GROUPS = ("mat", "rude", "soft")
DEFAULT_GROUPS = ("mat", "rude")

# Приставки перед корнем «хуй»: нахуй, похуй, охуеть, ахуеть, нихуя, дохуя, захуярить, схуяли…
_HUY_PREFIX = r"(?:на|по|о|а|ни|не|до|за|от|ото|рас|раз|при|вы|у|с|под|об|пере|недо|про)"
# «еб»: гласные приставки — как есть (заебал, наебал, уебок, долбоеб); согласные — только с ъ/ь
# («съебал», «въебал»), иначе «себя», «вебка», «подебоширить». «раз»/«рас»/«от» Whisper
# пишет и без ъ («разебал»), обычных слов с «разеб»/«отеб» нет.
_EB_PREFIX = (r"(?:за|на|по|до|у|вы|про|пере|недо|при|долбо|не|ни|хуе|раз[ъь]?|рас[ъь]?|от[ъь]?"
              r"|об[ъь]|с[ъь]|под[ъь]|в[ъь]|вз[ъь]|из[ъь])")

_RULES: list[tuple[str, re.Pattern]] = [
    # --- mat
    ("mat", re.compile(rf"^{_HUY_PREFIX}{{0,2}}ху[йяеиюё]")),
    ("mat", re.compile(r"п[ие]зд")),
    ("mat", re.compile(rf"^{_EB_PREFIX}{{0,2}}(?:еб|йоб)")),
    # «бля», «блядь», «блять», «выблядок»; «бляшка», «бляха» — обычные слова (бляха — в soft).
    ("mat", re.compile(r"^(?:вы|по|на|за)?бля(?![шх])")),
    # «мудак», «мудила», «мудозвон»; «мудрый», «мудрость» — нет.
    ("mat", re.compile(r"^(?:за|об|от)?муд(?:ак|ач|ил|ень|озвон|оеб|ох|ня|и$|е$|я$)")),
    ("mat", re.compile(r"залуп")),
    # «пидор», «пидарас», «пидрила», «педераст»; «Педро» — нет.
    ("mat", re.compile(r"^(?:за)?(?:пид[оа]р|пидр|пед[оа]р|педераст)")),
    ("mat", re.compile(r"^г[ао]нд[оа]н")),
    ("mat", re.compile(r"^шлю(?:х|шк|шечк)")),
    # --- rude: «сука», «суки», «сукин», «сучара», «сучка»; «сук» (ветка), «сукно», «сучок» — нет.
    ("rude", re.compile(r"^(?:сук(?:а|и|е|у|ой|ою|ам|ами|ах|ин|ина|ину|ины|иным|ино|енок|енка)"
                        r"|сучар\w*|сучк(?:а|е|у|ой|и)|суч(?:ий|ья|ье|ьи|ьего|ьей|ару))$")),
    # --- soft
    ("soft", re.compile(r"^блин")),
    ("soft", re.compile(r"^блях")),
    ("soft", re.compile(r"^(?:на|по|ни)?хрен")),
    # «херня», «нахер», «похер»; «Херсон», «херес», «херувим» — нет.
    ("soft", re.compile(r"^(?:на|по|ни|за)?хер(?!сон|ес|увим)")),
    ("soft", re.compile(r"^жоп")),
    ("soft", re.compile(r"^говн")),
    ("soft", re.compile(r"^дерьм")),
    # «срать», «насрать», «обосрался», «сраный»; «сразу», «сражение», «сравнить» — нет.
    ("soft", re.compile(r"^(?:на|обо|об|за|вы|по|у|рас|пере)?сра(?:т|л|н|ч|к)")),
    ("soft", re.compile(r"^(?:за|по|на|от|из)?дроч")),
]

# Обычные слова, которые всё-таки цепляет правило (держим явным списком — на них тесты).
_EXCEPTIONS = {
    "небо", "небе", "неба", "себя", "себе", "хлеб", "веб", "вебка",
    "рубля", "корабля", "сабля", "оскорбляю", "употреблять",
    "сук", "сукно", "сучок", "сучком",
    "мудрый", "мудро", "мудрость", "педро",
    "херсон", "херес", "сразу",
}

_PART_RE = re.compile(r"^([^\w*]*)([\w*]+?)([^\w*]*)$")
_HYPHENS = re.compile(r"([-‐‑])")


def _norm(core: str) -> str:
    return core.lower().replace("ё", "е")


def _is_starred(core: str) -> bool:
    """«б****», «бл*ть», «Б***» — уже записанный звёздочками мат."""
    return "*" in core and bool(re.search(r"[^\W\d_]", core))


def classify_part(core: str, groups: Any = DEFAULT_GROUPS) -> str | None:
    """Группа мата для одного слова без пунктуации (``mat``/``rude``/``soft``/``starred``) или None."""
    if not core:
        return None
    if _is_starred(core):
        return "starred"
    word = _norm(core)
    if not re.fullmatch(r"[а-я]+", word) or word in _EXCEPTIONS:
        return None
    wanted = set(groups or ())
    for group, pattern in _RULES:
        if group in wanted and pattern.search(word):
            return group
    return None


def _split(token: str) -> list[str]:
    """Слово по дефисам с самими дефисами: «пиздец-то» → [«пиздец», «-», «то»]."""
    return _HYPHENS.split(token)


def classify_token(token: Any, groups: Any = DEFAULT_GROUPS) -> str | None:
    """Группа мата слова субтитров (с пунктуацией, дефисными частями) или None."""
    for part in _split(str(token or "").strip()):
        m = _PART_RE.match(part)
        if m:
            group = classify_part(m.group(2), groups)
            if group:
                return group
    return None


def star_core(core: str) -> str:
    """Первая буква + звёздочки по длине: «Блять» → «Б****»; уже «б****» — как есть."""
    if not core or not re.match(r"[^\W\d_]", core):
        return core
    return core[0] + "*" * (len(core) - 1)


def star_token(token: str, groups: Any = DEFAULT_GROUPS) -> str:
    """Слово субтитров со звёздочками на месте мата, пунктуация и дефисные частицы на месте:
    «Блять,» → «Б****,», ««пиздец-то!»» → «п*****-то!». Повторно — то же самое (идемпотентно)."""
    out = []
    for part in _split(str(token)):
        m = _PART_RE.match(part)
        if m and classify_part(m.group(2), groups):
            out.append(m.group(1) + star_core(m.group(2)) + m.group(3))
        else:
            out.append(part)
    return "".join(out)


def find_profanity(words: list[dict], groups: Any = DEFAULT_GROUPS, skip: Any = ()) -> list[dict]:
    """Мат в словах субтитров: ``[{i, word, start, end, group}]`` (время — как у слов)."""
    skipped = {int(i) for i in skip or () if str(i).lstrip("-").isdigit()}
    found = []
    for i, w in enumerate(words or []):
        if i in skipped:
            continue
        group = classify_token(w.get("word"), groups)
        if not group:
            continue
        try:
            start, end = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            found.append({"i": i, "word": str(w.get("word") or ""), "start": start, "end": end, "group": group})
    return found


# --------------------------------------------------------------------------- #
# Глушение середины: уровни
# --------------------------------------------------------------------------- #

# Доли длительности слова (Whisper ставит границы слова с точностью ±50–100 мс):
# 1 — середина: начало и хвост слышны, слово узнаётся человеком, но не распознаётся;
# 2 — шире: слышны только самое начало и конец;
# 3 — почти целиком: слышна первая согласная (``head_sec``, не больше ``head_max`` слова — всегда
# уже, чем у уровня 2), хвост глушится с запасом ``pad_sec``.
LEVELS: dict[int, dict] = {
    1: {"from": 0.30, "to": 0.85},
    2: {"from": 0.20, "to": 0.95},
    3: {"head_sec": 0.08, "head_max": 0.15, "pad_sec": 0.04},
}
MAX_LEVEL = max(LEVELS)
MIN_SPAN_SEC = 0.06  # короче — щелчок, а не тишина (и меньше MIN_MUTE_SEC рендера)


def clamp_level(value: Any, default: int = 1) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_LEVEL, level))


def mute_range(start: float, end: float, level: int = 1, levels: dict | None = None) -> tuple[float, float]:
    """Отрезок тишины внутри слова ``start..end`` для уровня ``level`` (время — как у слова)."""
    table = levels or LEVELS
    spec = table[clamp_level(level)]
    start, end = float(start), float(end)
    dur = max(0.0, end - start)
    if "head_sec" in spec:
        head = min(float(spec["head_sec"]), dur * float(spec.get("head_max", 0.15)))
        a, b = start + head, end + float(spec.get("pad_sec", 0.0))
    else:
        a, b = start + dur * float(spec["from"]), start + dur * float(spec["to"])
    if b - a < MIN_SPAN_SEC:
        mid = (a + b) / 2
        a, b = mid - MIN_SPAN_SEC / 2, mid + MIN_SPAN_SEC / 2
    return round(max(0.0, a), 3), round(b, 3)


# --------------------------------------------------------------------------- #
# Звёздочки в субтитрах
# --------------------------------------------------------------------------- #


def star_subtitles(payload: dict, found: list[dict], groups: Any = DEFAULT_GROUPS) -> tuple[dict, int]:
    """Субтитры со звёздочками в найденных словах: слова и строки. (payload, сколько слов поменялось).

    Время слов не меняется (подсветка слов, караоке и номера слов остаются). Строка, текст
    которой собран из её слов, пересобирается из слов; переписанная владельцем — заменой того
    же слова в тексте."""
    from app.clip_subtitles import words_by_line

    words = [dict(w) for w in payload.get("words") or []]
    changed: dict[int, tuple[str, str]] = {}
    for item in found:
        i = int(item["i"])
        if not 0 <= i < len(words):
            continue
        old = str(words[i].get("word") or "")
        new = star_token(old, groups)
        if new != old:
            words[i]["word"] = new
            changed[i] = (old, new)
    if not changed:
        return payload, 0
    old_words = payload.get("words") or []
    lines = [dict(line) for line in payload.get("lines") or []]
    index_of = {id(w): k for k, w in enumerate(old_words)}
    for line, inside in zip(lines, words_by_line(lines, old_words)):
        ids = [index_of.get(id(w)) for w in inside]
        if not any(k in changed for k in ids):
            continue
        text = str(line.get("text") or "")
        joined = " ".join(str(w.get("word") or "") for w in inside)
        if re.sub(r"\s+", " ", joined).strip() == re.sub(r"\s+", " ", text).strip():
            line["text"] = " ".join(words[k]["word"] if k is not None else str(w.get("word") or "")
                                    for k, w in zip(ids, inside))
        else:
            for k in ids:
                if k in changed:
                    old, new = changed[k]
                    text = re.sub(rf"(?<![\w*]){re.escape(old)}(?![\w*])", new, text, count=1)
            line["text"] = text
    return {**payload, "words": words, "lines": lines}, len(changed)
