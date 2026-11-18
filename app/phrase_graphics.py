"""Тип фразы → графика: что показать на ключевой фразе сценария (объяснялка совы, промо, монтаж).

Идея (смысловые теги фраз → подбор графики) взята из разбора video-talkcraft (docs/research/video-repos-2026-11-17.md;
лицензия у них некоммерческая — код и тексты свои). Каждая фраза получает тип: число/статистика, сравнение, цитата,
шаги/список, до/после, представление героя или предмета, вопрос, вывод/итог, обычная фраза. На тип — 1–3 шаблона
галереи моушна (frontend/src/motion/templates/*.ts, ``add_motion_template``) с параметрами из самой фразы.

Правило владельца, ради которого всё это: новый элемент — только на КЛЮЧЕВОЙ фразе, не на каждом бите. ``pick_keys``
отбирает немного фраз (одна на ~7 с ролика, соседние фразы — не обе, тип подряд не повторяется, обычная фраза —
никогда). Остальным фразам графика не положена — у них работают позы, субтитры, камера.

Классификатор детерминированный (регулярки и слова для русского): работает без Gemini. Объяснялка даёт подсказки
планировщику (``app/explainer_gen_plan.py``) — Gemini принимает или игнорирует; агенты промо и монтажа зовут
``suggest_graphics(text | clip_id)``. Надписи — короткие фразы из самой речи (2–5 слов), не ярлыки-категории
(``promo-human-text``); число про деньги/бонусы помечено ``promo_caution`` (в промо сумм не обещаем).
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Типы и шаблоны
# --------------------------------------------------------------------------- #

# owl — годится поверх совы (плоский фон, поза на весь кадр): компактный, ставится вверху кадра и не закрывает
# маскота; карточки во весь кадр и размытие фона под плашками сове не подходят — её вид одобрен владельцем.
PHRASE_TYPES: dict[str, dict] = {
    "number": {"label": "число / статистика", "weight": 1.0,
               "templates": [("flat-stat", "карточка с числом, полоса и пояснение", False),
                             ("flat-counter", "пилюля: число накручивается", True),
                             ("counter", "крупное число с подписью", False),
                             ("flat-chip", "пилюля со временем или числом", True)]},
    "compare": {"label": "сравнение (A против B)", "weight": 0.95,
                "templates": [("flat-compare", "полосы «было/стало» с подписями", False),
                              ("bar-chart", "столбцы растут волной", False),
                              ("flat-chip", "пилюля «A или B»", True)]},
    "quote": {"label": "цитата", "weight": 0.9,
              "templates": [("quote-card", "карточка с кавычками и автором", False),
                            ("comment-card", "карточка комментария", False),
                            ("punchline", "фраза по словам на плашке", True)]},
    "steps": {"label": "шаги / список", "weight": 0.85,
              "templates": [("flat-checklist", "пункты с галочками по одному", False),
                            ("top-3", "три пункта с номерами", False),
                            ("flat-error", "чип с номером и заголовок", True)]},
    "before_after": {"label": "до / после", "weight": 0.95,
                     "templates": [("before-after", "разделитель «до | после»", False),
                                   ("flat-compare", "полосы было/стало", False),
                                   ("flat-chip", "пилюля «было → стало»", True)]},
    "intro": {"label": "представление героя или предмета", "weight": 0.7,
              "templates": [("lower-third-pill", "плашка-имя со значком", True),
                            ("lower-third-box", "имя на цветной плашке", False),
                            ("flat-chip", "пилюля с именем", True)]},
    "question": {"label": "вопрос", "weight": 0.55,
                 "templates": [("flat-hook", "слово-вопрос во всю ширину", False),
                               ("title-pop", "крупный капс выпрыгивает", False),
                               ("flat-chip", "пилюля с вопросом", True)]},
    "conclusion": {"label": "вывод / итог", "weight": 0.8,
                   "templates": [("punchline", "фраза по словам, ключевое подсвечено", True),
                                 ("keyword-big", "одно слово на весь экран", False),
                                 ("flat-chip", "пилюля с выводом", True)]},
    "plain": {"label": "обычная фраза", "weight": 0.0, "templates": []},
}
TYPE_ORDER = ("quote", "before_after", "compare", "number", "steps", "intro", "conclusion", "question", "plain")
KEY_EVERY_SEC = 7.0  # одна графика примерно на столько секунд ролика
KEY_EVERY_PHRASES = 3  # без времени — одна на столько фраз
CHARS_PER_SEC = 15.0  # оценка длины фразы без озвучки (как у сценария объяснялки)

# --------------------------------------------------------------------------- #
# Слова и регулярки (русский)
# --------------------------------------------------------------------------- #

_NUM_WORDS = (
    "ноль один одна одно два две три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать "
    "четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать двадцать тридцать сорок пятьдесят "
    "шестьдесят семьдесят восемьдесят девяносто сто двести триста четыреста пятьсот шестьсот семьсот восемьсот "
    "девятьсот тысяча тысячи тысяч миллион миллиона миллионов миллиард полтора полторы половина треть четверть "
    "двое трое четверо пятеро вдвое втрое вчетверо двух трёх трех четырёх четырех пяти шести семи восьми девяти "
    "десяти двадцати тридцати сорока ста"
).split()
_NUM_VALUES = {"ноль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "двое": 2, "вдвое": 2, "три": 3,
               "трое": 3, "втрое": 3, "четыре": 4, "четверо": 4, "вчетверо": 4, "пять": 5, "пятеро": 5, "шесть": 6,
               "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
               "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
               "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200, "триста": 300,
               "пятьсот": 500, "двух": 2, "трёх": 3, "трех": 3, "четырёх": 4, "четырех": 4, "пяти": 5,
               "шести": 6, "семи": 7, "восьми": 8, "девяти": 9, "десяти": 10, "двадцати": 20, "тридцати": 30,
               "сорока": 40, "ста": 100, "тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "миллион": 1_000_000,
               "миллиона": 1_000_000, "миллионов": 1_000_000, "полтора": 1.5, "полторы": 1.5, "половина": 50,
               "треть": 33, "четверть": 25}
_UNIT_RE = (r"(%|процент\w*|раз\w*|час\w*|минут\w*|секунд\w*|лет\b|год\w*|дн(?:я|ей)\b|недел\w*|месяц\w*|"
            r"руб\w*|₽|\$|долл\w*|тысяч\w*|миллион\w*|миллиард\w*|кг\b|км\b|литр\w*|л\b|шаг\w*|человек\b|"
            r"подписчик\w*|просмотр\w*|клиент\w*|балл\w*|бонус\w*)")
_DIGIT_NUM = re.compile(r"(?<![\w#№])([+−-]?\d[\d\s]{0,12}(?:[.,]\d+)?)\s*" + _UNIT_RE + r"?", re.IGNORECASE)
_WORD_NUM = re.compile(r"\b(" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")\b\s+" + _UNIT_RE,
                       re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{1,2}[:.]\d{2})\b")
# «каждый пятый», «каждая третья» — доля людей/случаев: тоже статистика.
_EVERY_RE = re.compile(r"\bкажд\w+\s+(втор|трет|четвёрт|четверт|пят|шест|седьм|восьм|девят|десят|сот)\w*\b", re.IGNORECASE)
_EVERY_VALUES = {"втор": 2, "трет": 3, "четвёрт": 4, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8,
                 "девят": 9, "десят": 10, "сот": 100}
_POINT_RE = re.compile(r"\b(номер|пункт|шаг|ошибка|совет|способ|правило)\s+(номер\s+)?(один|два|три|четыре|пять|\d)\b",
                       re.IGNORECASE)
_MONEY_RE = re.compile(r"руб|₽|\$|долл|бонус|балл|скидк|выплат|заработ|доход|кэшбэк", re.IGNORECASE)

_QUOTE_RE = re.compile(r"[«\"“„]([^«»\"“”„]{6,160})[»\"”“]")
_SAID_RE = re.compile(r"\b(сказал\w*|говорит|говорил\w*|пишет|писал\w*|по словам|цитат\w*|как говорится)\b",
                      re.IGNORECASE)
_BEFORE_AFTER_RE = re.compile(
    r"\b(до и после|было\b.*\bстало|раньше\b.*\b(теперь|сейчас|стало)|был[аио]?\b.*\bа теперь|"
    r"через (неделю|месяц|год|день)|после этого|с тех пор)\b", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\s(vs\.?|против|versus)\s|\bлучше,? чем\b|\bхуже,? чем\b|\bбыстрее,? чем\b|"
                         r"\bв отличие от\b|\bвместо\b|\bразница между\b|\bили\b.{2,40}\?|,\s*а не\b|\bне\s+\w+,\s*а\s",
                         re.IGNORECASE)
_STEPS_RE = re.compile(r"\b(во-первых|во-вторых|в-третьих|сначала\b.*\b(потом|затем)|шаг\w*|по порядку|"
                       r"(три|четыре|пять|\d)\s+(способ\w*|ошиб\w*|совет\w*|шаг\w*|правил\w*|причин\w*|приём\w*))\b",
                       re.IGNORECASE)
_INTRO_RE = re.compile(r"\b(знакомьтесь|знакомься|познакомься|познакомьтесь|встречайте|встречай|меня зовут|"
                       r"его зовут|её зовут|это\s+[—-]|представляю|называется|вот он\b|вот она\b|это\s+[А-ЯЁA-Z]\w+)",
                       re.UNICODE)
_QUESTION_START = re.compile(r"^\s*(а\s+)?(почему|зачем|как|что|сколько|когда|где|кто|неужели|знаешь|знали|хочешь|"
                             r"бывало|бывает|замечал\w*|ты\s+тоже)\b", re.IGNORECASE)
_CONCLUSION_RE = re.compile(r"\b(итог\w*|итак|вывод\w*|короче|в общем|главное|запомни\w*|получается|"
                            r"значит|так что|в сухом остатке|резюм\w*|подытож\w*)\b", re.IGNORECASE)
_CONCLUSION_LEAD = re.compile(r"^\W*(итак|в итоге|итог\w*|короче|в общем|вывод\w*|главное|запомни\w*|"
                              r"так что|получается)\b", re.IGNORECASE)
_CTA_RE = re.compile(r"\b(подпишись|подписывайся|ставь лайк|жми|ссылка в профиле|переходи)\b", re.IGNORECASE)
_STOP = set("и в во на с со к ко по за из у о об от до не ни но а же ли бы то это что как так там тут ещё уже "
            "очень всё все вот ты вы мы он она они я мне тебе твой твоя свой своя его её их".split())


# --------------------------------------------------------------------------- #
# Классификация
# --------------------------------------------------------------------------- #


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _words(text: str) -> list[str]:
    return [w.strip(".,!?;:«»\"“”()—–-") for w in _clean(text).split() if w.strip(".,!?;:«»\"“”()—–-")]


def _short(text: str, n: int = 5) -> str:
    """2–5 слов из самой фразы (надпись на экране — словами речи, не ярлыком): без служебных слов по краям."""
    words = _words(text)
    while words and words[0].lower() in _STOP and len(words) > 2:
        words = words[1:]
    words = words[:n]
    while len(words) > 1 and words[-1].lower() in _STOP:
        words = words[:-1]
    return " ".join(words)


def _cut(text: str, limit: int) -> str:
    """Надпись не длиннее ``limit`` символов — по целым словам."""
    out = ""
    for w in str(text).split():
        if len(out) + len(w) + (1 if out else 0) > limit:
            break
        out = f"{out} {w}" if out else w
    return out or str(text)[:limit]


def key_word(text: str) -> str:
    """Ключевое слово фразы — для слова-удара (keyword-big, flat-hook)."""
    from app.word_emphasis import key_word_score

    words = _words(text)
    scored = sorted(((key_word_score(w), -i, w) for i, w in enumerate(words)), reverse=True)
    return scored[0][2] if scored and scored[0][0] > 0 else (words[-1] if words else "")


def _number(text: str) -> dict | None:
    """Число фразы: значение, единица, слово, на котором оно звучит."""
    if _POINT_RE.search(text):  # «ошибка номер два» — номер пункта, не статистика
        stripped = _POINT_RE.sub(" ", text)
    else:
        stripped = text
    m = _TIME_RE.search(stripped)
    if m:
        return {"value": m.group(1).replace(".", ":"), "unit": "", "word": m.group(1), "time": True}
    for m in _DIGIT_NUM.finditer(stripped):
        raw = m.group(1).strip()
        digits = re.sub(r"\s", "", raw).replace(",", ".").replace("−", "-")
        if not re.search(r"\d", digits):
            continue
        try:
            value: float = float(digits)
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        if not unit and abs(value) < 10 and "." not in digits:
            continue  # одиночная цифра без единицы — чаще номер, а не статистика
        return {"value": int(value) if value == int(value) else value, "unit": unit, "word": raw.split()[0]}
    m = _EVERY_RE.search(stripped)
    if m:
        n = _EVERY_VALUES[m.group(1).lower()]
        return {"value": round(100 / n), "unit": "%", "word": m.group(0).split()[-1], "every": n}
    m = _WORD_NUM.search(stripped)
    if m:
        word = m.group(1).lower()
        return {"value": _NUM_VALUES.get(word, word), "unit": m.group(2).lower(), "word": m.group(1)}
    return None


def _sides(text: str) -> tuple[str, str] | None:
    """Две стороны сравнения: «A против B», «A, а не B», «вместо A — B», «A или B?»."""
    t = _clean(text).rstrip(".!?")
    for pat in (r"(.+?)\s+(?:vs\.?|против|versus)\s+(.+)", r"(.+?),\s*а не\s+(.+)", r"(.+?)\s+или\s+(.+)",
                r"(.+?)\s+(?:лучше|хуже|быстрее|дешевле|дороже),?\s+чем\s+(.+)", r"не\s+(.+?),\s*а\s+(.+)"):
        m = re.match(pat, t, re.IGNORECASE)
        if m and m.group(1).strip() and m.group(2).strip():
            return _short(m.group(1), 3), _short(m.group(2), 3)
    m = re.search(r"вместо\s+(.+?)[,—–-]+\s*(.+)", t, re.IGNORECASE)
    if m:
        return _short(m.group(1), 3), _short(m.group(2), 3)
    return None


def _items(text: str) -> list[str]:
    """Пункты списка: после двоеточия через запятую/«и», или «сначала…, потом…, затем…»."""
    t = _clean(text).rstrip(".!?")
    if ":" not in t and not re.search(r"\b(потом|затем)\b", t, re.IGNORECASE):
        return []
    tail = t.split(":", 1)[1] if ":" in t else t
    parts = [p for p in re.split(r",\s*|\s+и\s+|\s*;\s*|\b(?:потом|затем|после)\b", tail) if p and p.strip()]
    parts = [_short(p, 4) for p in parts if len(_words(p)) >= 1]
    return [p for p in parts if p][:5] if len(parts) >= 2 else []


def classify(text: str) -> dict:
    """Тип фразы и данные для шаблона: ``{type, label, confidence, why, fields}``. Обычная фраза — ``plain``."""
    t = _clean(text)
    found: dict[str, tuple[float, str, dict]] = {}
    if not t:
        return _result("plain", 0.0, "пусто", {})
    quote = _QUOTE_RE.search(t)
    if quote and (len(_words(quote.group(1))) >= 3):
        author = ""
        said = (re.search(r"(?:говорил|сказал|писал|пишет|говорит)\w*\s+([А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)?)", t)
                or re.search(r"(?:—|-)\s*([А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)?)\s*$", t)
                or re.search(r"([А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)?)\s+(?:сказал|говорил|писал)", t))
        if said:
            author = said.group(1)
        found["quote"] = (0.95, "текст в кавычках", {"text": quote.group(1).strip(), "author": author})
    elif _SAID_RE.search(t) and ":" in t:
        found["quote"] = (0.6, "«сказал: …»", {"text": t.split(":", 1)[1].strip(" «»\""), "author": ""})
    if _BEFORE_AFTER_RE.search(t):
        sides = _sides(t) or ("До", "После")
        found["before_after"] = (0.85, "было/стало, раньше/теперь", {"left": sides[0], "right": sides[1]})
    if _COMPARE_RE.search(t):
        sides = _sides(t)
        if sides:
            found["compare"] = (0.8, "две стороны: " + " / ".join(sides), {"a": sides[0], "b": sides[1]})
    num = _number(t)
    if num is not None:
        conf = 0.9 if num.get("unit") or num.get("time") else 0.65
        found["number"] = (conf, f"число {num['value']}{(' ' + num['unit']) if num.get('unit') else ''}",
                           {**num, "label": _short(_DIGIT_NUM.sub(" ", t) if num.get("word", "").isdigit() else t, 4),
                            **({"promo_caution": True} if _MONEY_RE.search(t) else {})})
    if _STEPS_RE.search(t) or _POINT_RE.search(t) or (":" in t and len(_items(t)) >= 3):
        point = _POINT_RE.search(t)
        found["steps"] = (0.8 if point or len(_items(t)) >= 3 else 0.6, "пункт или список",
                          {"title": _short(t.split(":", 1)[0], 4), "items": _items(t),
                           **({"chip": f"{point.group(1).capitalize()} {point.group(3)}"} if point else {})})
    intro = _INTRO_RE.search(t)
    if intro:
        name = (re.search(r"\b(?:это|зовут|называется)\s*[—-]?\s*([«\"]?[А-ЯЁA-Z][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*)?)", t)
                or re.search(r"(?:зовут|называется|знакомьтесь|знакомься|встречайте|встречай|представляю)"
                             r"[,:]?\s+([«\"]?[А-ЯЁA-Zа-яё][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*)?)", t, re.IGNORECASE))
        found["intro"] = (0.75, "представление", {"name": (name.group(1).strip("«\"") if name else _short(t, 3)),
                                                   "sub": _short(t, 5)})
    lead = bool(_CONCLUSION_LEAD.match(t))
    if _CONCLUSION_RE.search(t) and not _CTA_RE.search(t):
        found["conclusion"] = (0.75, "вывод", {"text": _short(re.sub(r"^\W*(итак|короче|в общем|итог\w*|вывод\w*)"
                                                                      r"[,:—–-]*\s*", "", t, flags=re.IGNORECASE),
                                                                5), "word": key_word(t)})
    if t.endswith("?") or _QUESTION_START.search(t):
        found["question"] = (0.7 if t.endswith("?") else 0.5, "вопрос", {"text": _short(t, 5), "word": key_word(t)})
    # «Итак, …», «Главное — …» в начале фразы — это вывод, даже если внутри сравнение или число.
    order = ("quote", "conclusion", *[k for k in TYPE_ORDER if k not in ("quote", "conclusion")]) if lead else TYPE_ORDER
    for kind in order:
        if kind in found:
            conf, why, fields = found[kind]
            return _result(kind, conf, why, fields, also=[k for k in order if k in found and k != kind])
    return _result("plain", 0.0, "без числа, сравнения, цитаты, списка, вопроса и вывода", {})


def _result(kind: str, conf: float, why: str, fields: dict, also: list[str] | None = None) -> dict:
    out = {"type": kind, "label": PHRASE_TYPES[kind]["label"], "confidence": round(conf, 2), "why": why,
           "fields": fields}
    if also:
        out["also"] = also
    return out


# --------------------------------------------------------------------------- #
# Параметры шаблонов из фразы
# --------------------------------------------------------------------------- #


def _num_value(fields: dict) -> float | int | None:
    value = fields.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else float(value)


def template_params(template: str, kind: str, fields: dict, text: str) -> dict:
    """Параметры шаблона галереи из данных фразы (остальные — по умолчанию у шаблона)."""
    t = _clean(text)
    short = _short(t, 5)
    value = _num_value(fields)
    unit = str(fields.get("unit") or "")
    if template in ("flat-stat", "counter", "flat-counter", "odometer-counter") and value is not None:
        suffix = "%" if unit.startswith(("%", "процент")) else ""
        label = fields.get("label") or short
        if template == "flat-stat":
            return {"label": label, "value": value, "suffix": suffix, "delta": ""}
        if template == "counter":
            return {"to": value, "label": label, "suffix": suffix}
        if template == "odometer-counter":
            return {"to": value, "label": label, "suffix": suffix, "caption": ""}
        return {"value": value, "from": 0, "unit": suffix or unit or label}
    if template == "flat-compare":
        a, b = (fields.get("a"), fields.get("b")) if kind == "compare" else (fields.get("left"), fields.get("right"))
        return {"label": short, "rows": f"{a or 'было'} | 35 | ; {b or 'стало'} | 70 | ", "max": 80, "marker": -1}
    if template == "bar-chart":
        return {"title": short, "labels": f"{fields.get('a') or 'A'}, {fields.get('b') or 'B'}", "values": "40, 80",
                "arrow": False}
    if template == "before-after":
        return {"left": fields.get("left") or fields.get("a") or "До", "right": fields.get("right") or fields.get("b")
                or "После"}
    if template == "quote-card":
        return {"text": fields.get("text") or t, "author": fields.get("author") or ""}
    if template == "comment-card":
        return {"text": fields.get("text") or t, "likes": 0, "likesTo": 0}
    if template == "punchline":
        body = fields.get("text") or short
        words = _words(body)
        word = fields.get("word") or key_word(body)
        idx = next((i for i, w in enumerate(words) if w.lower() == str(word).lower()), len(words) - 1)
        return {"text": body, "word": max(0, idx)}
    if template == "flat-checklist":
        items = fields.get("items") or []
        return {"title": fields.get("title") or short, "items": "; ".join(items) if items else short, "app": ""}
    if template == "top-3":
        items = (fields.get("items") or [])[:3]
        out = {"title": fields.get("title") or short}
        for n, item in enumerate(items, 1):
            out[f"item{n}"] = item
        return out
    if template == "flat-error":
        return {"chip": fields.get("chip") or "", "title": fields.get("title") or short}
    if template in ("lower-third-pill", "lower-third-box"):
        return {"title": fields.get("name") or short, "subtitle": ""}
    if template in ("flat-hook", "keyword-big"):
        word = fields.get("word") or key_word(t)
        return {"word": word} if template == "flat-hook" else {"text": word}
    if template == "title-pop":
        return {"text": fields.get("text") or short}
    if template == "flat-chip":
        if kind == "compare":
            return {"text": _cut(f"{fields.get('a')} или {fields.get('b')}", 28)}
        if kind == "before_after":
            return {"text": _cut(f"{fields.get('left')} → {fields.get('right')}", 28)}
        if kind == "intro":
            return {"text": _cut(str(fields.get("name") or short), 28)}
        if kind == "number":
            unit_text = unit if unit.startswith("%") else (f" {unit}" if unit else "")
            return {"text": _cut(f"{fields.get('value')}{unit_text}", 20)}
        return {"text": _cut(str(fields.get("text") or short), 28)}
    return {}


def candidates(kind: str, fields: dict, text: str, owl: bool = False) -> list[dict]:
    """1–3 шаблона на тип с параметрами; ``owl`` — только годные поверх совы."""
    out = []
    specs = list(PHRASE_TYPES.get(kind, {}).get("templates", []))
    if kind == "steps" and len(fields.get("items") or []) < 2:
        # Пункт без перечня («Ошибка номер один: …») — чип с номером и заголовок, список не из чего собрать.
        specs = [s for s in specs if s[0] == "flat-error"]
    for template, what, owl_ok in specs:
        if owl and not owl_ok:
            continue
        if kind == "number" and template in ("flat-stat", "counter", "flat-counter") and _num_value(fields) is None:
            continue  # время «07:00» — не накрутка числа: его покажет пилюля
        out.append({"template": template, "what": what, "params": template_params(template, kind, fields, text)})
    return out[:3]


# --------------------------------------------------------------------------- #
# Ключевые фразы
# --------------------------------------------------------------------------- #


def pick_keys(phrases: list[dict], duration: float | None = None) -> list[int]:
    """Номера фраз, где появится новый элемент. Одна графика на ~KEY_EVERY_SEC с (без времени — на
    KEY_EVERY_PHRASES фраз), обычная фраза — никогда, соседние фразы — не обе, один тип подряд — только первый,
    при равенстве — раньше в ролике. ``phrases``: ``[{type, confidence, start?, end?}]``."""
    if not phrases:
        return []
    if duration is None and all(p.get("end") is not None for p in phrases):
        duration = float(phrases[-1]["end"]) - float(phrases[0].get("start") or 0.0)
    limit = max(1, round(duration / KEY_EVERY_SEC)) if duration else max(1, round(len(phrases) / KEY_EVERY_PHRASES))
    scored = sorted(((PHRASE_TYPES[p["type"]]["weight"] * float(p.get("confidence") or 0), -i, i)
                     for i, p in enumerate(phrases) if p["type"] != "plain"), reverse=True)
    chosen: list[int] = []
    for score, _neg, i in scored:
        if len(chosen) >= limit or score <= 0.3:
            break
        if any(abs(i - j) <= 1 for j in chosen):
            continue
        # Один тип подряд (между ними нет другой графики) — нет: два счётчика подряд читаются как повтор.
        before = max((j for j in chosen if j < i), default=None)
        after = min((j for j in chosen if j > i), default=None)
        kind = phrases[i]["type"]
        if any(j is not None and phrases[j]["type"] == kind for j in (before, after)):
            continue
        chosen.append(i)
    return sorted(chosen)


# --------------------------------------------------------------------------- #
# Фразы из текста или клипа
# --------------------------------------------------------------------------- #

_SENT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_phrases(text: str) -> list[dict]:
    """Текст → фразы с оценкой времени по длине (~15 символов речи в секунду)."""
    out, t = [], 0.0
    for part in _SENT_RE.split(str(text or "")):
        part = _clean(part)
        if not part:
            continue
        dur = max(0.8, len(part) / CHARS_PER_SEC)
        out.append({"text": part, "start": round(t, 2), "end": round(t + dur, 2), "timing": "estimate"})
        t += dur
    return out


def phrases_from_words(words: list[dict], gap: float = 0.55) -> list[dict]:
    """Слова субтитров с временем → фразы: конец предложения или пауза дольше ``gap``."""
    out: list[dict] = []
    cur: list[dict] = []
    for w in words or []:
        word = str(w.get("word") or "").strip()
        if not word:
            continue
        if cur and float(w.get("start") or 0) - float(cur[-1].get("end") or 0) > gap:
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", word):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"text": " ".join(str(w["word"]).strip() for w in ws), "start": round(float(ws[0]["start"]), 3),
             "end": round(float(ws[-1]["end"]), 3), "timing": "speech"} for ws in out]


def suggest(phrases: list[dict], owl: bool = False, duration: float | None = None) -> list[dict]:
    """Тип и шаблоны на каждую фразу; ``key`` — только у отобранных ``pick_keys`` (там и стоит ставить графику)."""
    rows = []
    for i, ph in enumerate(phrases):
        info = classify(ph["text"])
        rows.append({"i": i, **{k: ph[k] for k in ("text", "start", "end", "timing") if k in ph}, **info})
    keys = set(pick_keys(rows, duration))
    for row in rows:
        row["key"] = row["i"] in keys
        row["candidates"] = candidates(row["type"], row["fields"], row["text"], owl=owl) if row["key"] else []
    return rows


def _clip_phrases(store: Any, clip_id: int) -> tuple[list[dict], float | None]:
    subs = store.get_clip_plan_subtitles(int(clip_id)) or {}
    words = [w for w in subs.get("words") or [] if str(w.get("word") or "").strip()]
    return phrases_from_words(words), None


def suggest_graphics(store: Any = None, text: str = "", clip_id: int | None = None, owl: bool = False) -> dict:
    """Подсказки графики по тексту или по речи клипа (слова субтитров с временем)."""
    if str(text or "").strip():
        phrases, source = split_phrases(text), "text"
    elif clip_id is not None and store is not None:
        phrases, _ = _clip_phrases(store, int(clip_id))
        source = "clip"
        if not phrases:
            raise ValueError("в клипе нет слов субтитров — дай text или сначала generate_subtitles")
    else:
        raise ValueError("нужен text или clip_id")
    rows = suggest(phrases, owl=owl)
    keys = [r for r in rows if r["key"]]
    return {
        "source": source, "phrases": rows, "key_phrases": [r["i"] for r in keys],
        "rule": "новый элемент — только на ключевой фразе (key); остальным графика не нужна: позы, субтитры, камера",
        "notes": ([f"фраза {r['i']}: число про деньги/бонусы — в промо сумм не обещаем (promo-no-promises)"
                   for r in keys if r["fields"].get("promo_caution")]
                  + (["время фраз — оценка по длине текста (15 символов/с); у клипа — по словам речи"]
                     if source == "text" else [])),
        "next": "add_motion_template(template, params, at = start ключевой фразы) — параметры уже из фразы; "
                "надпись — словами самой речи, 2–5 слов",
    }


# --------------------------------------------------------------------------- #
# Инструмент агента
# --------------------------------------------------------------------------- #


def tools() -> list:
    from app.tool_core import _obj, Tool, ToolError

    def h_suggest_graphics(ctx: Any, args: dict) -> dict:
        clip_id = args.get("clip_id") or getattr(ctx, "clip_plan_id", None)
        try:
            return suggest_graphics(ctx.store, str(args.get("text") or ""),
                                    int(clip_id) if clip_id not in (None, "") else None, owl=bool(args.get("owl")))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return [Tool(
        "suggest_graphics", "Подбираю графику к фразам",
        "Тип каждой фразы сценария или речи клипа (число/статистика, сравнение, цитата, шаги/список, до/после, "
        "представление героя, вопрос, вывод, обычная) и 1–3 шаблона галереи с параметрами ИЗ ФРАЗЫ. Новый элемент — "
        "ТОЛЬКО на ключевых фразах (key: одна на ~7 с, не подряд, тип не повторяется), остальным графика не нужна. "
        "text — сценарий/текст промо (время — оценка по длине); без text — речь клипа clip_id (время — по словам "
        "субтитров). owl — только компактное, что не закрывает маскота. Ставить — add_motion_template(template, "
        "params, at = start). / Phrase type → motion template suggestions for key phrases only.",
        _obj({"text": {"type": "string", "description": "текст сценария или промо; пусто — речь клипа"},
              "clip_id": {"type": "integer", "description": "клип, чью речь разобрать (без text)"},
              "owl": {"type": "boolean", "description": "только компактная графика поверх маскота"}}),
        h_suggest_graphics, scope="global",
        label=lambda a: "Подбираю графику к тексту" if a.get("text") else "Подбираю графику к речи клипа",
    )]
