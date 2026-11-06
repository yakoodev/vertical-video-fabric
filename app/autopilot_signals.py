"""Автопилот: сигналы правил в ответах владельца (из ``app/autopilot.py``)."""

from __future__ import annotations

import re

# Признак правила на будущее: обобщение («всегда», «больше не», «такие моменты»), оценка типа
# контента («такое заходит»), «не бери / не ставь», явное «запомни».
_GENERAL_RE = re.compile(
    r"(всегда|никогда|больше не|впредь|в будущем|на будущее|каждый раз|в каждом|во всех|по умолчанию|"
    r"в следующий раз|в этом проекте|на этом канале|у нас на канале|вообще|"
    r"(^|[^\w])(такие|такое|таких|таким)(?=[^\w]|$)(?!\s+же)|"
    r"(не )?заход(ит|ят)|залетает|не залетает|"
    r"не бери|не берите|не брать|не ставь|не ставьте|не ставить|не делай так|не надо так|"
    r"так и делай|(^|[^\w])делай так(?=[^\w]|$)|так держать|почаще|пореже|побольше|поменьше|"
    r"запомни|запиши правило|возьми за правило|always|never|from now on|remember)", re.IGNORECASE)
# Разовая команда: повелительный глагол про конкретный объект («убери …», «удали этот …», «сделай …»).
_COMMAND_RE = re.compile(
    r"(^|[.!?,;:—\-]\s*|\s(и|а|потом|ещё|еще|теперь)\s+)(убери|уберите|удали|удалите|сделай|сделайте|поставь|"
    r"поставьте|переделай|переделайте|перемонтируй|добавь|добавьте|вставь|обрежь|обрежьте|подрежь|вырежи|вырежьте|"
    r"сократи|замени|поменяй|сдвинь|подвинь|исправь|поправь|заглуши|запикай|приглуши|увеличь|уменьши|"
    r"смонтируй|сгенерируй|покажи|найди|открой|опубликуй|отправь|запусти|нарежь|скачай|склей|верни|откати|"
    r"перенеси|выкинь|выкиньте|почисти|наложи|подставь|переименуй|назови)(?=[^\w]|$)", re.IGNORECASE)
# Указание на конкретное: этот/здесь/тут, номер клипа, секунда, таймкод.
_DEICTIC_RE = re.compile(
    r"((^|[^\w])(этот|эта|эту|этого|этой|этом|эти|этих|здесь|тут|вот тут|вот здесь)(?=[^\w]|$)|#\s?\d+|"
    r"клип[а-я]*\s+\d+|\d+:\d\d|\d+(?:[.,]\d+)?\s?(с|сек|секунд[а-я]*)(?=[^\w]|$))", re.IGNORECASE)


_PROJECT_SCOPE_RE = re.compile(r"(в этом проекте|на этом канале)", re.IGNORECASE)


def rule_signal(text: str) -> str:
    """general — есть признак правила на будущее; command — разовая команда к конкретному
    (повелительный глагол или «этот/здесь/тут», номер клипа, секунда) без признака обобщения;
    neutral — ни того ни другого (спорно: «без мата», «слишком длинно»)."""
    raw = str(text or "")
    if _GENERAL_RE.search(raw):
        return "general"
    deictic = _DEICTIC_RE.search(_PROJECT_SCOPE_RE.sub(" ", raw))
    if _COMMAND_RE.search(raw) or deictic:
        return "command"
    return "neutral"


_PRAISE_FILLER_RE = re.compile(
    r"(круто|так и делай|так держать|так и надо|отлично|супер|класс|огонь|продолжай|в том же духе|"
    r"нравится|мне|пожалуйста|плиз|плз|пж|и переделай|переделай|перемонтируй|давай)", re.IGNORECASE)


def _bare(text: str) -> str:
    return re.sub(r"[\s!.…,)]+$", "", str(text or "").strip().lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+|\n+", str(text or "")) if s.strip()]


def _rule_text_of(sentence: str, kind: str) -> str:
    text = sentence.strip().strip("«»\"'")
    if kind == "do":
        text = _PRAISE_FILLER_RE.sub(" ", text)
        text = re.sub(r"^\W*(что|чтобы|когда)\s+", "", text.strip(" ,.—-!"), flags=re.IGNORECASE)
    else:
        text = re.sub(r"\b(пожалуйста|плиз|плз|пж|и переделай|переделай)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,.—-!")
    return (text[:1].upper() + text[1:])[:300] if text else ""


def describe_learning(result: dict | None) -> str:
    """Человеческая строка для ответа владельцу в Telegram/UI."""
    if not result:
        return ""
    action = result.get("action")
    if action == "rule_added":
        return f"Запомнил правило #{result['rule']['id']}: «{result['rule']['text']}»."
    if action == "duplicate" and result.get("rule"):
        return f"Такое правило уже есть: #{result['rule']['id']} «{result['rule']['text']}»."
    if action == "rule_proposed":
        return f"Записать правило «{result['text']}»? Карточка #{result['proposal_id']}."
    if action == "conflict_proposed":
        return f"{result.get('question') or 'Противоречит старому правилу.'} Карточка #{result.get('proposal_id')}."
    if action == "revoke_proposed":
        return f"Отменить правило #{result['rule_id']}? Карточка #{result['proposal_id']}."
    if action == "revoke_not_found":
        return result.get("note") or ""
    return ""
