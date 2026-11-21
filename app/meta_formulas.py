"""Названия, описания и теги по формулам каналов нарезок (findings.md п. 43–52), без модели.

* RU-реакция: «<НИК> СМОТРИТ: <название оригинала дословно>»; первая строка описания — «Оригинал — <ссылка>» (43, 47);
* RU-подборка-реакция: «<НИК> СМОТРИТ: <тема> / <БРЕНД>» (47);
* RU-игровая: «<НИК> <ГЛАГОЛ-провал> <ЧТО> В <ИГРА>» капсом (45);
* EN: «<Streamer>'s Most LEGENDARY Moments of <год>», строчное «clips that made <x> famous» (49, 52);
* шортс: КАПС + 2 хэштега ника (кир. + лат.), строка «Весь ролик — <ссылка>» (44);
* теги — детерминированный генератор вариантов ника: транслит, э/е, лишняя «е», ник с цифрами,
  «X реакция» / «реакция X», «<ник> смотрит <объект>» (47, 48).

``check_title`` / ``check_tags`` — проверки (название совпадает с оригиналом по нормализованной строке, теги
без повторов и в лимите YouTube 500 символов).
"""

from __future__ import annotations

import re
from typing import Iterable

YT_TAGS_LIMIT = 500
YT_TITLE_LIMIT = 100
_RU2LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
           "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
           "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
           "э": "e", "ю": "yu", "я": "ya"}
_LAT2RU = [("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("ts", "ц"), ("yu", "ю"), ("ya", "я"), ("kh", "х"),
           ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"), ("i", "и"),
           ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"),
           ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "в"), ("x", "кс"), ("y", "й"), ("z", "з")]


def normalize(text: str) -> str:
    """Для сравнения названий: строчные, ё→е, без пунктуации и лишних пробелов."""
    return " ".join(re.findall(r"[\wа-яё]+", (text or "").lower().replace("ё", "е")))


def translit(text: str) -> str:
    return "".join(_RU2LAT.get(ch, ch) for ch in (text or "").lower())


def to_cyrillic(text: str) -> str:
    s = (text or "").lower()
    out = ""
    i = 0
    while i < len(s):
        for lat, ru in _LAT2RU:
            if s.startswith(lat, i):
                out += ru
                i += len(lat)
                break
        else:
            out += s[i]
            i += 1
    return out


def _clip(title: str, limit: int = YT_TITLE_LIMIT) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def reaction_title(nick: str, original_title: str) -> str:
    return _clip(f"{nick.strip().upper()} СМОТРИТ: {original_title.strip()}")


def reaction_compilation_title(nick: str, theme: str, brand: str) -> str:
    return _clip(f"{nick.strip().upper()} СМОТРИТ: {theme.strip()} / {brand.strip().upper()}")


def game_fail_title(nick: str, verb_past: str, what: str, place: str, game: str) -> str:
    return _clip(f"{nick} {verb_past} {what} В {place} {game}".upper())


def legendary_title(streamer: str, year: int | str) -> str:
    return _clip(f"{streamer.strip()}'s Most LEGENDARY Moments of {year}")


def famous_title(streamer: str) -> str:
    return _clip(f"clips that made {streamer.strip()} famous".lower())


def short_title(text: str, nick_ru: str, nick_lat: str) -> str:
    """Шортс: капс + 2 хэштега ника (кириллица и латиница)."""
    tags = f"#{re.sub(r'[^a-zа-яё0-9_]', '', nick_ru.lower())} #{re.sub(r'[^a-z0-9_]', '', nick_lat.lower())}"
    return _clip(f"{text.strip().upper()} {tags}")


def reaction_description(original_url: str, body: str = "") -> str:
    return "\n".join(x for x in (f"Оригинал — {original_url.strip()}", body.strip()) if x)


def short_description(full_url: str, body: str = "") -> str:
    return "\n".join(x for x in (f"Весь ролик — {full_url.strip()}", body.strip()) if x)


def nick_variants(nick: str, aliases: Iterable[str] = ()) -> list[str]:
    """Варианты ника: как есть, кириллица↔латиница, э↔е, лишняя «е» в конце, без цифр и с цифрами из алиасов."""
    base = [n.strip().lower() for n in [nick, *aliases] if n and n.strip()]
    out: list[str] = []

    def add(v: str) -> None:
        v = v.strip().lower()
        if v and v not in out:
            out.append(v)

    for n in base:
        add(n)
        core = re.sub(r"\d+$", "", n)
        add(core)
        if re.search(r"[а-яё]", core):
            add(translit(core))
            add(core.replace("э", "е"))
            add(core.replace("е", "э", 1) if core.startswith("е") else core)
        else:
            ru = to_cyrillic(core)
            add(ru)
            add(ru.replace("е", "э", 1) if ru.startswith("е") else ru.replace("э", "е"))
            if ru.startswith("е"):
                add("э" + ru[1:])
            if core.endswith("e"):
                add(core[:-1])
                add(to_cyrillic(core[:-1]))
            else:
                add(core + "e")
                add(ru + "е")
    return out


def genitive(word: str) -> str:
    """Родительный падеж ника для «реакция X»: эвелон → эвелона, мура → муры, братишкин → братишкина."""
    w = word.strip().lower()
    if not re.search(r"[а-я]$", w):
        return w
    if w.endswith(("а", "я")):
        return w[:-1] + ("и" if w[-2:-1] in "гкхжчшщ" or w.endswith("я") else "ы")
    if w.endswith("й"):
        return w[:-1] + "я"
    if w.endswith("ь"):
        return w[:-1] + "я"
    if w[-1] in "еиоуыэю":
        return w
    return w + "а"


def tag_list(nick: str, aliases: Iterable[str] = (), obj: str | None = None, limit: int = YT_TAGS_LIMIT,
             extra: Iterable[str] = ()) -> list[str]:
    """Детерминированный набор тегов: варианты ника, «X реакция» / «реакция X», «X смотрит <объект>»,
    «<объект> X»; в лимите YouTube (сумма символов + запятые ≤ 500)."""
    variants = nick_variants(nick, aliases)
    main = variants[0] if variants else ""
    ru_main = next((v for v in variants if re.search(r"[а-я]", v) and not re.search(r"\d", v)), main)
    tags: list[str] = []
    for v in variants:
        tags.append(v)
    tags += [f"{ru_main} реакция", f"реакция {genitive(ru_main)}", f"{main} reaction"]
    if obj:
        o = obj.strip().lower()
        tags += [f"{ru_main} смотрит {o}", f"{o} {ru_main}", f"{o} реакция {ru_main}", f"{o} реакция"]
    tags += [t.strip().lower() for t in extra if t and t.strip()]
    out: list[str] = []
    used = 0
    for t in tags:
        if t in out:
            continue
        cost = len(t) + (1 if out else 0)
        if used + cost > limit:
            break
        out.append(t)
        used += cost
    return out


def check_title(title: str, original_title: str | None = None, nick: str | None = None) -> list[str]:
    problems = []
    if len(title) > YT_TITLE_LIMIT:
        problems.append(f"название {len(title)} символов > {YT_TITLE_LIMIT}")
    if original_title is not None and normalize(original_title) not in normalize(title):
        problems.append("название оригинала не совпадает дословно")
    if nick and nick.lower() not in title.lower():
        problems.append("нет ника в названии")
    return problems


def check_tags(tags: list[str], nick: str) -> list[str]:
    problems = []
    if len(set(tags)) != len(tags):
        problems.append("повторы тегов")
    total = sum(len(t) for t in tags) + max(0, len(tags) - 1)
    if total > YT_TAGS_LIMIT:
        problems.append(f"теги {total} символов > {YT_TAGS_LIMIT}")
    if not any(re.search(r"[a-z]", t) for t in tags) or not any(re.search(r"[а-я]", t) for t in tags):
        problems.append("нет ника и латиницей, и кириллицей")
    if not any("реакция" in t for t in tags):
        problems.append("нет «X реакция»/«реакция X»")
    if nick.lower() not in tags:
        problems.append("нет самого ника")
    return problems
