"""«Мувик под песню» как работа сервиса: карточка «Новое видео» → чат проекта с плейбуком frag-movie и первым заданием.

Раньше мувик (бои записей Apex под песню владельца, текст песни стилизованными субтитрами, части для TikTok) делал
только наш агент руками по плейбуку ``docs/playbooks/frag-movie.md``. Теперь:

* ``start(store, project_id, …)`` — записи (источники проекта), песня (трек библиотеки музыки или ссылка), текст
  песни (необязательно). Текст запоминается у трека (``track_lyrics:<id>`` в app_settings — ``align_lyrics`` берёт
  его сам, если текст не передали), проекту ставятся настройки работы (``workflow``, ниже), заводится переписка
  чата проекта с закреплённым плейбуком (``chat_threads.playbook``) и первым заданием; агент начинает сразу.
* Настройки работы проекта — ключ ``workflow`` в настройках автопилота проекта (app/autopilot_config.py):
  ``caption_template`` и ``hashtags`` — подпись публикации (её берут ``plan_tiktok_parts`` для каждой части и
  метаданные публикации, app/ai/publish_meta.py), ``tiktok_parts`` — части ≤ 58 с песни, равной длины, «Часть N/M»,
  ``freeze_check`` — после рендера сразу прогон на застывшие куски (app/render_jobs.py).

Как у «Промо» и «Объяснялки», карточка только запускает работу: шаги и проверки — у агента и в плейбуке.
"""

from __future__ import annotations

import re
from typing import Any

WORKFLOW_KIND = "frag_movie"
PLAYBOOK = "frag-movie"
CAPTION_TEMPLATE = "ʏᴀᴋᴏᴏᴅᴇᴠ ⨯ ᴀᴘᴇx ⨯ {track} · {n}/{m}"
HASHTAGS = ("#apex", "#apexlegends", "#апекс", "#мувик", "#fragmovie", "#gaming")
TIKTOK_PARTS = {"max_song_sec": 58.0, "equal": True, "label": "Часть {n}/{m}", "end_text": "Часть {next} → в профиле"}
DEFAULTS: dict = {"kind": WORKFLOW_KIND, "playbook": PLAYBOOK, "caption_template": CAPTION_TEMPLATE,
                  "hashtags": list(HASHTAGS), "tiktok_parts": dict(TIKTOK_PARTS), "freeze_check": True}
LYRICS_KEY = "track_lyrics:{id}"
MAX_LYRICS = 12000
LANGS = ("", "ru", "en", "uk", "es", "de", "fr", "pt", "it", "ja", "ko")

_SMALL = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))
_PART_SUFFIX = re.compile(r"\s*·\s*\{n\}\s*/\s*\{m\}")


def small_caps(text: Any) -> str:
    """Латиница → маленькие капители («Stay Together» → «ꜱᴛᴀʏ ᴛᴏɢᴇᴛʜᴇʀ»); кириллица и цифры как есть."""
    return "".join(_SMALL.get(ch, ch) for ch in str(text or "").lower())


def song_title(label: Any) -> str:
    """Название песни из подписи трека: «Артист - Песня» → «Песня», без расширения и хвостовых знаков."""
    text = re.sub(r"\.(wav|mp3|m4a|flac|ogg|opus|aac)$", "", " ".join(str(label or "").split()), flags=re.I)
    for sep in (" — ", " – ", " - "):
        if sep in text:
            text = text.split(sep, 1)[1]
            break
    return text.strip(" !?.,;:«»\"'")


def render_caption(template: str, track: str = "", n: int | None = None, m: int | None = None) -> str:
    """Подпись по шаблону: ``{track}`` — название песни маленькими капителями, ``{n}/{m}`` — номер части (нет части —
    « · N/M» убирается); трека нет — «⨯ {track}» убирается."""
    text = str(template or CAPTION_TEMPLATE)
    if n is None or m is None:
        text = _PART_SUFFIX.sub("", text)
    title = small_caps(song_title(track))
    if not title:
        text = re.sub(r"\s*⨯\s*\{track\}", "", text)
    return " ".join(text.replace("{track}", title).replace("{n}", str(n or "")).replace("{m}", str(m or "")).split())


def caption_text(workflow: dict, track: str = "", n: int | None = None, m: int | None = None) -> str:
    """Подпись целиком — строка подписи и строка хэштегов (для блока «скопировать»)."""
    tags = " ".join(workflow.get("hashtags") or [])
    return "\n".join(x for x in (render_caption(workflow.get("caption_template") or "", track, n, m), tags) if x)


# --------------------------------------------------------------------------- #
# Настройки работы проекта (ключ workflow настроек автопилота)
# --------------------------------------------------------------------------- #


def normalize_workflow(raw: Any) -> dict:
    """Пусто — у проекта нет особой работы. Иначе — DEFAULTS вида + то, что задано (неизвестное отбрасывается)."""
    if not isinstance(raw, dict) or raw.get("kind") != WORKFLOW_KIND:
        return {}
    out = {**DEFAULTS, "tiktok_parts": dict(TIKTOK_PARTS)}
    if isinstance(raw.get("caption_template"), str) and raw["caption_template"].strip():
        out["caption_template"] = " ".join(raw["caption_template"].split())[:200]
    if isinstance(raw.get("hashtags"), list):
        tags = [re.sub(r"[^0-9a-zа-яё_]", "", re.sub(r"\s+", "", str(x or "")).lstrip("#").lower())
                for x in raw["hashtags"]]
        out["hashtags"] = list(dict.fromkeys(f"#{t}" for t in tags if t))[:12]
    parts = raw.get("tiktok_parts") if isinstance(raw.get("tiktok_parts"), dict) else {}
    try:
        if parts.get("max_song_sec") is not None:
            out["tiktok_parts"]["max_song_sec"] = min(60.0, max(10.0, float(parts["max_song_sec"])))
    except (TypeError, ValueError):
        pass
    if "equal" in parts:
        out["tiktok_parts"]["equal"] = bool(parts["equal"])
    for key in ("label", "end_text"):
        if isinstance(parts.get(key), str) and parts[key].strip():
            out["tiktok_parts"][key] = " ".join(parts[key].split())[:80]
    if "freeze_check" in raw:
        out["freeze_check"] = bool(raw["freeze_check"])
    if raw.get("track_id") not in (None, ""):
        try:
            out["track_id"] = int(raw["track_id"])
        except (TypeError, ValueError):
            pass
    return out


def project_workflow(store: Any, project_id: int | None) -> dict:
    if not project_id:
        return {}
    try:
        from app.autopilot_config import get_config

        return normalize_workflow(get_config(store, int(project_id)).get("workflow"))
    except (KeyError, ValueError, TypeError):
        return {}


def plan_workflow(store: Any, clip_plan_id: int | None) -> dict:
    """Настройки работы проекта клипа (нет проекта или работы — {})."""
    if not clip_plan_id:
        return {}
    try:
        from app import projects as P

        return project_workflow(store, P.project_of_plan(store, int(clip_plan_id)))
    except KeyError:
        return {}


def set_workflow(store: Any, project_id: int, patch: dict | None = None) -> dict:
    from app.autopilot_config import get_config, update_config

    current = get_config(store, int(project_id)).get("workflow") or {}
    merged = {**DEFAULTS, **(current if isinstance(current, dict) else {}), **(patch or {}), "kind": WORKFLOW_KIND}
    return update_config(store, int(project_id), {"workflow": normalize_workflow(merged)})["workflow"]


# --------------------------------------------------------------------------- #
# Текст песни у трека
# --------------------------------------------------------------------------- #


def save_track_lyrics(store: Any, track_id: int, text: str, lang: str = "") -> None:
    store.get_audio_track(int(track_id))
    store.set_app_setting(LYRICS_KEY.format(id=int(track_id)),
                          {"text": str(text or "").replace("\r", "").strip()[:MAX_LYRICS], "lang": lang})


def track_lyrics(store: Any, track_id: int | None) -> dict:
    """{text, lang} текста песни трека (сохранён карточкой мувика) или {}."""
    if not track_id:
        return {}
    value = store.get_app_setting_value(LYRICS_KEY.format(id=int(track_id)))
    return value if isinstance(value, dict) and value.get("text") else {}


# --------------------------------------------------------------------------- #
# Старт работы
# --------------------------------------------------------------------------- #


def _source_line(store: Any, source: dict) -> str:
    from app import nav

    title = nav.titles_for(store, [source]).get(int(source["id"]), "") or source.get("original_filename") or ""
    minutes = float(source.get("duration_sec") or 0) / 60
    return f"#{source['id']} «{title}»" + (f", {minutes:.0f} мин" if minutes >= 1 else "")


def first_task(*, title: str, sources: list[str], track_id: int, track_label: str, lyrics: bool, lang: str,
               workflow: dict, notes: str = "") -> str:
    parts = workflow.get("tiktok_parts") or TIKTOK_PARTS
    lang_arg = f', lang: "{lang}"' if lang else ""
    lines = [
        f"Мувик под песню «{title}» — плейбук frag-movie (он уже в контексте, следуй ему по шагам).",
        "Записи: " + "; ".join(sources) + ".",
        f"Песня: трек #{track_id} «{track_label}»"
        + (" — текст песни владельца сохранён у трека." if lyrics else " — текста нет, строки распознай сам."),
    ]
    if notes:
        lines.append(f"Пожелания владельца: {notes}")
    lines += [
        "",
        "Первый ход:",
        "1. game_events(source_id) по каждой записи (группа game — подгружена с плейбуком) и source_contact_sheet "
        "по окнам "
        "боёв — хроника боёв: от первого "
        "урона до последнего нока или уничтожения отряда, каждый нок внутри. Пришли список боёв (запись, время, что "
        "в нём) и какие берёшь и в каком порядке.",
        f"2. align_lyrics(track_id: {track_id}{lang_arg}) — строки песни со временем по слогам вокала "
        "(без lyrics — возьмёт сохранённый текст). Сомнительные строки (unsure) не показываем.",
        "3. Две 20-секундные пробы на утверждение (стиль текста и переходы) — монтаж: новый клип create_clip из "
        f"сильного боя, музыка set_music(track_id: {track_id}), склейки в доли snap_to_beats, переходы "
        "set_transition(type: \"frag\"), lyric_layers (текст только в паузах после киллов), render_clip → "
        "render_status. Пришли обе. Полный рендер — только после «ок».",
        "",
        "Потом по плейбуку: полная версия, заставка с логотипом (бренд проекта) — только на длинной версии; части "
        f"для TikTok — plan_tiktok_parts (≤ {parts.get('max_song_sec', 58):g} с песни, части одной длины, "
        f"«{parts.get('label')}», подписи — caption в ответе, из настроек проекта); после рендера — проверка на "
        "застывшие куски (lint_clip freezes). Подписи — блоком для копирования. Каждая готовая версия (пробы, "
        "полная 16:9 и 9:16, части) — в «Ленту»: feed_create_work один раз, дальше feed_add_version с вариантами.",
    ]
    return "\n".join(lines)


def start(store: Any, project_id: int, *, title: str, source_ids: list[int], track_id: int | None = None,
          music_url: str = "", lyrics: str = "", lang: str = "", notes: str = "", run_agent: bool = True) -> dict:
    """Запустить мувик: проверить записи и песню, запомнить текст у трека и настройки работы проекта, завести
    переписку с плейбуком и первым заданием. Ответ: {thread_id, to, track_id, workflow, started}."""
    from app import project_chat as C
    from app import projects as P
    from app.project_chat_threads import set_thread_playbook

    project = P.get_project(store, int(project_id), include_sources=True)
    title = " ".join(str(title or "").split())[:100]
    if not title:
        raise ValueError("нужно название мувика")
    lang = str(lang or "").strip().lower()
    if lang not in LANGS:
        raise ValueError(f"язык песни — один из {', '.join(x for x in LANGS if x)} (или пусто — сам)")
    own = {int(s["id"]): s for s in project.get("sources") or []}
    wanted = [int(s) for s in dict.fromkeys(source_ids or [])]
    if not wanted:
        raise ValueError("выбери записи игры — хотя бы одну")
    foreign = [s for s in wanted if s not in own]
    if foreign:
        raise ValueError(f"записи {', '.join(f'#{s}' for s in foreign)} не из этого проекта")
    if track_id in (None, "", 0) and str(music_url or "").strip():
        from app.music_import import import_music_from_url

        track_id = int(import_music_from_url(store, str(music_url).strip(), "")["id"])
    if track_id in (None, "", 0):
        raise ValueError("выбери песню из музыки, загрузи файл или дай ссылку")
    track = store.get_audio_track(int(track_id))
    text = str(lyrics or "").strip()
    if len(text) > MAX_LYRICS:
        raise ValueError(f"текст песни длиннее {MAX_LYRICS} знаков")
    if text:
        save_track_lyrics(store, int(track["id"]), text, lang)
    has_lyrics = bool(text or track_lyrics(store, int(track["id"])))
    workflow = set_workflow(store, int(project_id), {"track_id": int(track["id"])})
    label = str(track.get("label") or track.get("original_filename") or f"трек {track['id']}")
    task = first_task(title=title, sources=[_source_line(store, own[s]) for s in wanted], track_id=int(track["id"]),
                      track_label=label, lyrics=has_lyrics, lang=lang, workflow=workflow,
                      notes=" ".join(str(notes or "").split())[:600])
    thread = C.create_thread(store, int(project_id), title=f"Мувик: {title}")
    set_thread_playbook(store, int(thread["id"]), PLAYBOOK)
    started = False
    if run_agent:
        C.send_to_thread(store, int(thread["id"]), task, origin="ui", actor="frag_movie",
                         attachments=[{"kind": "source", "id": s} for s in wanted])
        started = True
    return {"thread_id": int(thread["id"]), "to": f"/chat/{thread['id']}", "track_id": int(track["id"]),
            "track": label, "lyrics": has_lyrics, "workflow": workflow, "started": started, "task": task,
            "caption_example": caption_text(workflow, label, 1, 3)}


__all__ = ["CAPTION_TEMPLATE", "DEFAULTS", "HASHTAGS", "PLAYBOOK", "WORKFLOW_KIND", "caption_text", "first_task",
           "normalize_workflow", "plan_workflow", "project_workflow", "render_caption", "save_track_lyrics",
           "set_workflow", "small_caps", "song_title", "start", "track_lyrics"]
