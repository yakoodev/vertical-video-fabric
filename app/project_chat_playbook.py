"""Плейбук хода в чате проекта: подставляется в системный промпт по смыслу запроса.

Эксперимент 12.11: Claude прочитал stream-shorts и сделал хорошую нарезку, Gemini в чате проекта ни разу не вызвал
get_playbook — центр-кроп игры без лица и донат-бар. ИИ-монтажёр клипа давно подставляет плейбук сам
(``app/ai/editor_agent_prompts.active_playbook``), чат проекта — нет. Теперь тоже:

1. последний успешный ``get_playbook(name)`` хода или имя плейбука в запросе — как у монтажёра;
2. плейбук, закреплённый за перепиской (``chat_threads.playbook``: карточка «Мувик под песню», app/frag_movie.py) —
   «ок, делай полную» плейбука по словам не назовёт, а работа та же;
3. иначе — по словам запроса: AMV / аниме-клип / аниме-эдит → ``amv``, клип на песню → ``music-video``,
   мувик / фраг-мувик / бои под песню → ``frag-movie``,
   нарезка стрима →
   ``stream-shorts``, реклама сайта/товара → ``ad-voiceover``, серия по комменту / эпизод → заметка ``SERIES_NOTE``
   (плейбука серии ещё нет).

Выбранный плейбук ещё и подгружает свои узкие группы инструментов (``preload_groups``: game, web, amv, feed…),
а про большие общие (montage, audio, cuts…) в промпте сказано, какие звать по шагу (``groups_note``). Какие
группы — по разделу «## Инструменты MCP» плейбука (``playbook_tools``): правка плейбука меняет и подгрузку.
"""

from __future__ import annotations

import re

# Реклама и серия — только если в запросе нет стрима/VOD/эфира: «нарежь рекламную вставку из стрима» — это нарезка.
_STREAM = re.compile(r"(стрим|vod|эфир|трансляц|нарез|шортс|shorts|вертикалк|клип(ы|ов)? из)", re.I)
_AD = re.compile(r"(реклам|промо(?!код)|продающ|ролик про (сайт|магазин|товар|сервис)|прорекламир)", re.I)
_SERIES = re.compile(r"(сери[юяи]\b|серию|эпизод|по коммент|зрители реш|интерактивн)", re.I)
_SCREEN_REC = re.compile(r"(запис[ьи] экрана|скринкаст|screen ?rec)", re.I)
# Мувик: бои записей игры под песню, без мемов (с мемами — meme-edit, его зовут по имени).
_FRAG = re.compile(r"(мувик|фраг[- ]?мув|frag ?movie|нарезк\w* бо[её]в под (песн|трек|музык)|бои под (песн|трек))", re.I)
_MEMES = re.compile(r"(мем|meme)", re.I)
# Клип на песню (пресет 13.11: картинки в 2.5D, большие слова на слогах) — не мувик из записей игры.
_MUSIC_VIDEO = re.compile(r"(клип\w* (на|под) (сво\w+ )?песн|музыкальн\w* клип|music ?video)", re.I)
# AMV: кадры аниме/фильма (один тайтл) под песню — плейбук amv (карточка «AMV», app/amv.py).
_AMV = re.compile(r"(\bamv\b|\bамв\b|аниме[- ]?клип|аниме[- ]?эдит|anime ?(music ?video|edit)|эдит\w* (по )?аниме)",
                  re.I)

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
7. contact_sheet глазами → в «Ленту»: feed_add_version(variants [{label, render_clip_id}]) — плейбук handoff.
series3d_* для проекта «Мир от зрителей» (PS1) не подходит — только холст.
"""


def pick(message: str, steps: list[dict] | None = None, pinned: str | None = None) -> str | None:
    """Имя плейбука хода (или ``"series"`` — заметка серии), None — без подсказки. ``pinned`` — плейбук переписки."""
    from app.ai.editor_agent_prompts import active_playbook

    named = active_playbook(message, steps)
    if named:
        return named
    if pinned:
        return str(pinned)
    text = str(message or "")
    if _AMV.search(text):  # раньше «клипа на песню»: «аниме-клип под песню» — AMV из кадров тайтла, не картинки
        return "amv"
    if _MUSIC_VIDEO.search(text):
        return "music-video"
    if _FRAG.search(text) and not _MEMES.search(text):
        return "frag-movie"
    if _AD.search(text) and not _STREAM.search(text):
        return "app-promo" if _SCREEN_REC.search(text) else "ad-voiceover"
    if _SERIES.search(text) and not _STREAM.search(text):
        return "series"
    if _STREAM.search(text):
        return "stream-shorts"
    return None


# Заметке серии (не файл плейбука) — её инструменты: холст, картинки/сцены слоями, голос, музыка.
SERIES_TOOLS = ("get_project_brief", "create_canvas_clip", "add_layers", "add_voiceover", "check_voiceover",
                "set_music", "auto_mix", "lint_clip", "render_clip", "render_status", "contact_sheet",
                "feed_add_version")
_TOOL_NAME = re.compile(r"`([a-z][a-z0-9_]+)`")


def playbook_tools(name: str | None) -> list[str]:
    """Инструменты плейбука: раздел «## Инструменты MCP», а нет раздела — все `имена` в тексте, что есть в чате."""
    if not name:
        return []
    if name == "series":
        return list(SERIES_TOOLS)
    from app import chat_tools as CT
    from app import playbooks

    try:
        text = playbooks.get_playbook(name)["text"]
    except KeyError:
        return []
    if "## Инструменты MCP" in text:
        text = text.split("## Инструменты MCP", 1)[1].split("\n## ", 1)[0]
    known = CT.all_tools()
    return list(dict.fromkeys(n for n in _TOOL_NAME.findall(text) if n in known))


def groups_for(name: str | None) -> list[str]:
    """Группы инструментов чата, без которых плейбук не сделать: подгружаются сами, как только плейбук выбран
    (закреплённый «ок, делай полную» не называет ни одного инструмента — раньше агент шёл без них или гадал
    группу). Только группы, где лежат инструменты плейбука, — не всё подряд: каждая группа — токены в каждом ходе."""
    from app import chat_tools as CT

    base = set(CT.base_names())
    groups = [CT.group_of(n) for n in playbook_tools(name) if n not in base]
    return list(dict.fromkeys(g for g in groups if g and g != "other"))


# Общие большие группы (десятки объявлений, 15–50 тыс. знаков каждая) сами не подгружаем: модель зовёт их по шагу
# плейбука (load_tools) или по словам владельца. Узкие (game, web, amv, music_video, feed, voice…) — сразу:
# их имён модель не угадает (12.11 и 16.11: «группа other» в первом задании мувика, реклама без web_search).
HEAVY_GROUPS = frozenset({"montage", "audio", "cuts", "subtitles", "sources", "screens", "assets"})


def preload_groups(name: str | None) -> list[str]:
    """Что подгрузить сразу, когда выбран плейбук: его узкие группы."""
    return [g for g in groups_for(name) if g not in HEAVY_GROUPS]


def groups_note(name: str | None) -> str:
    groups = groups_for(name)
    if not groups:
        return ""
    later = [g for g in groups if g in HEAVY_GROUPS]
    now = [g for g in groups if g not in HEAVY_GROUPS]
    parts = []
    if now:
        parts.append("уже подгружены: " + ", ".join(now))
    if later:
        parts.append("остальные по шагу — load_tools([" + ", ".join(f'"{g}"' for g in later) + "])")
    return "Инструменты этого плейбука в группах: " + "; ".join(parts) + ".\n"


def block(name: str | None) -> str:
    """Текст для системного промпта: плейбук целиком (как у монтажёра) или заметка серии + где его инструменты."""
    if not name:
        return ""
    if name == "series":
        return SERIES_NOTE + groups_note(name)
    from app.ai.editor_agent_prompts import playbook_block

    text = playbook_block(name)
    return text + groups_note(name) if text else ""
