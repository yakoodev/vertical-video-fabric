"""Плейбук хода в чате проекта: подставляется в системный промпт по смыслу запроса.

Эксперимент 12.11: Claude прочитал stream-shorts и сделал хорошую нарезку, Gemini в чате проекта ни разу не вызвал
get_playbook — центр-кроп игры без лица и донат-бар. ИИ-монтажёр клипа давно подставляет плейбук сам
(``app/ai/editor_agent_prompts.active_playbook``), чат проекта — нет. Теперь тоже:

1. последний успешный ``get_playbook(name)`` хода или имя плейбука в запросе — как у монтажёра;
2. иначе — по словам запроса: нарезка стрима → ``stream-shorts``, реклама сайта/товара → ``ad-voiceover``,
   серия по комменту / эпизод → заметка ``SERIES_NOTE`` (плейбука серии ещё нет).
"""

from __future__ import annotations

import re

# Реклама и серия — только если в запросе нет стрима/VOD/эфира: «нарежь рекламную вставку из стрима» — это нарезка.
_STREAM = re.compile(r"(стрим|vod|эфир|трансляц|нарез|шортс|shorts|вертикалк|клип(ы|ов)? из)", re.I)
_AD = re.compile(r"(реклам|промо(?!код)|продающ|ролик про (сайт|магазин|товар|сервис)|прорекламир)", re.I)
_SERIES = re.compile(r"(сери[юяи]\b|серию|эпизод|по коммент|зрители реш|интерактивн)", re.I)
_SCREEN_REC = re.compile(r"(запис[ьи] экрана|скринкаст|screen ?rec)", re.I)

SERIES_NOTE = """
ЗАДАНИЕ — СЕРИЯ (эпизод по комменту зрителя)
Плейбука серии в сервисе пока нет. Порядок, который работает:
1. get_project_brief(project_id) — лор, прошлые серии (covered), narrator_voice; что выбрали зрители — из
   комментария владельца дословно.
2. Сценарий 8–12 коротких фраз в стиле прошлых серий: завязка по комменту, действие, вопрос зрителям в конце.
3. create_canvas_clip(project_id) → картинка/сцена (слой scene или image) на каждую фразу.
4. Голос — add_voiceover(clip_id, lines) (без style; голос рассказчика из брифа) → check_voiceover.
5. Карточка коммента зрителя, бросок d20/выбор, вопрос в финале (титры 2–5 слов).
6. set_music → auto_mix → lint_clip → render_clip → render_status (done и без «озвучка с ошибками»).
series3d_* для проекта «Мир от зрителей» (PS1) не подходит — только холст.
"""


def pick(message: str, steps: list[dict] | None = None) -> str | None:
    """Имя плейбука хода (или ``"series"`` — заметка серии), None — без подсказки."""
    from app.ai.editor_agent_prompts import active_playbook

    named = active_playbook(message, steps)
    if named:
        return named
    text = str(message or "")
    if _AD.search(text) and not _STREAM.search(text):
        return "app-promo" if _SCREEN_REC.search(text) else "ad-voiceover"
    if _SERIES.search(text) and not _STREAM.search(text):
        return "series"
    if _STREAM.search(text):
        return "stream-shorts"
    return None


def block(name: str | None) -> str:
    """Текст для системного промпта: плейбук целиком (как у монтажёра) или заметка серии."""
    if not name:
        return ""
    if name == "series":
        return SERIES_NOTE
    from app.ai.editor_agent_prompts import playbook_block

    return playbook_block(name)
