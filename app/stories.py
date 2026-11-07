"""Истории: озвученные сюжетные шортсы (рассказчик + сцена на каждую фразу) — данные, библиотека
сцен, звуки и деньги (docs/guides/stories.md).

Пример владельца — 3D-«игрушечная» история: простые персонажи с круглыми головами, уютные 3D-комнаты
(суд, тюрьма, банк), рассказчик смешно рассказывает (побег из тюрьмы), на КАЖДУЮ фразу — новая
неподвижная сцена в одном стиле с мягким движением камеры (наезд, панорама, параллакс), мелкие
акценты (мультяшные штрихи движения, «zzz», трещина в стене), звуки на действиях (храп, скрежет,
будильник — только настоящие записи), субтитры по ОДНОМУ слову в нижней трети; 25–30 кадров в минуту.

Сущности (проект вида «ИИ-видео»: деньги, карточки и работник генерации — общие с
``app/aivideo_gen.py``):

* мир (``story_worlds``) — стиль (русская подпись + английский паспорт, ДОСЛОВНО в каждый промпт),
  рассказчик (характер для сценариста), голос TTS (Gemini — бесплатно), музыка, вид субтитров;
  референсы стиля — версии ``aiv_refs`` (target ``story_world``, kind ``story_style``): загрузка или
  один «кадр стиля», нарисованный в карточке первого эпизода;
* актёры (``story_characters``) — простые персонажи мира с листом (``story_char`` / ``story_sheet``):
  лист — референс в каждой сцене с этим персонажем, поэтому персонаж узнаваем из эпизода в эпизод;
* локации (``story_locations``) — плиты-фоны без людей (``story_loc`` / ``story_plate``);
* библиотека сцен (``story_scenes``) — локация + состав актёров + действие (английский текст) + теги +
  акцент; картинка 9:16 — ``story_scene`` / ``story_scene``. Сцена переиспользуется, когда у бита то же
  место и те же актёры, а действие по тегам похоже (Жаккар ≥ ``REUSE_MIN``) — новая не рисуется;
* эпизод (``story_episodes``) — завязка, длительность, тон, статус конвейера, сценарий с кадрами на
  каждую фразу, биты, слова озвучки, итоговый клип. Конвейер и сборка — ``app/stories_gen.py``.

Деньги — расширение этапа ``stories`` у ``app/aivideo_gen.py``: новые сцены, листы, плиты, кадр стиля и
озвучка Polza — ОДНОЙ карточкой на эпизод (потолок ``STORIES_EPISODE_CAP_RUB``, 200 ₽); Gemini TTS и
сухой прогон (явная опция эпизода ``dry_run``) — сразу, 0 ₽.

Где что лежит. Здесь — серии и расширение ИИ-видео. Вынесено: библиотека мира (миры, персонажи, локации, сцены) ``app/stories_library.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import explainer as E
from app import projects as P
from app.settings import settings

from app.stories_library import (  # noqa: F401
    STYLE_KIND, SHEET_KIND, PLATE_KIND, SCENE_KIND, T_WORLD, T_CHAR, T_LOC, T_SCENE, T_EP, ACTIVE_STATUSES, DRAFT_STATUS, ORIGINS,
    REUSE_MIN, STYLE_REFS_MAX, DEFAULT_STYLE_LABEL, DEFAULT_STYLE, FORMAT_STORY, FORMAT_DISASTER, FORMATS,
    DEFAULT_CAPTIONS, StoryError, _loads, _json, _tags, _drop_refs, _ref_brief, _image_asset, WORLD_FIELDS,
    MUSIC_MODES, _captions, _world_view, style_refs, get_world, list_worlds, save_world, mark_world_stale,
    delete_world, add_style_ref, world_voice, is_dry, scene_usable, _char_view, _loc_view, get_character,
    list_characters, get_location, list_locations, save_character, save_location, _stale_scenes, _scene_in_use,
    delete_character, delete_location, sheet_path, plate_path, _cast, _scene_view, get_scene, list_scenes,
    save_scene, delete_scene, mark_used, scene_terms, scene_similarity, match_scene,
)

STORY_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS story_worlds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        style_label TEXT NOT NULL DEFAULT '',
        style_passport TEXT NOT NULL DEFAULT '',
        negative TEXT NOT NULL DEFAULT '',
        narrator TEXT NOT NULL DEFAULT '',
        voice_json TEXT NOT NULL DEFAULT '{}',
        music_track_id INTEGER,
        captions_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        world_id INTEGER NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        identity_card TEXT NOT NULL DEFAULT '',
        persona TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        origin TEXT NOT NULL DEFAULT 'agent',
        uses INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (world_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        world_id INTEGER NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        origin TEXT NOT NULL DEFAULT 'agent',
        uses INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (world_id, slug)
    )
    """,
    # Сцена: место + состав (отсортированные id актёров) + действие. Картинка — версия aiv_refs.
    """
    CREATE TABLE IF NOT EXISTS story_scenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        world_id INTEGER NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
        slug TEXT NOT NULL DEFAULT '',
        location_id INTEGER,
        cast_json TEXT NOT NULL DEFAULT '[]',
        title TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        accent TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        origin TEXT NOT NULL DEFAULT 'generated',
        uses INTEGER NOT NULL DEFAULT 0,
        last_used_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_story_scenes_world ON story_scenes(world_id, location_id)",
    # Эпизод. status: new → scripted → planned → awaiting_approval → generating → assembling → done |
    # failed | cancelled (как у объяснялок).
    """
    CREATE TABLE IF NOT EXISTS story_episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        world_id INTEGER NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
        premise TEXT NOT NULL DEFAULT '',
        duration_target REAL NOT NULL DEFAULT 50,
        tone TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        title TEXT NOT NULL DEFAULT '',
        script_json TEXT NOT NULL DEFAULT '{}',
        beats_json TEXT NOT NULL DEFAULT '[]',
        words_json TEXT NOT NULL DEFAULT '[]',
        timeline_json TEXT NOT NULL DEFAULT '{}',
        options_json TEXT NOT NULL DEFAULT '{}',
        notes_json TEXT NOT NULL DEFAULT '[]',
        approval_id INTEGER,
        clip_id INTEGER,
        estimate_rub REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_story_episodes_status ON story_episodes(status, id)",
]
# Колонки, добавленные после первых запусков (старые базы получают их ALTER TABLE).
STORY_COLUMNS = [
    # Жанр-шаблон мира по умолчанию: story — смешная история, disaster — сериал-катастрофа
    # (app/stories_disaster.py); эпизод может переопределить (options.format).
    ("story_worlds", "format", "TEXT NOT NULL DEFAULT 'story'"),
    # Музыка в ролике: '' — как обычно (трек мира/проекта), none — не вшивать (катастрофа: музыку
    # ставят в TikTok из библиотеки площадки), auto — вшивать и у катастрофы.
    ("story_worlds", "music", "TEXT NOT NULL DEFAULT ''"),
]

STAGE = "stories"


NARRATION_KIND = "story_narration"


STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling", "done", "failed",
            "cancelled", DRAFT_STATUS)


STATUS_LABELS = {"new": "пишу сценарий", "scripted": "подбираю сцены", "planned": "считаю стоимость",
                 "awaiting_approval": "ждёт одобрения", "generating": "рисую сцены и озвучку",
                 "assembling": "собираю клип", "done": "готово", "failed": "ошибка", "cancelled": "отменено",
                 DRAFT_STATUS: "только сценарий, не запущен"}
TONES = ("смешно", "с иронией", "абсурдно", "уютно", "драматично", "напряжённо")
DURATION_MIN = 30.0
DURATION_MAX = 75.0


MAX_TAGS = 16


FORMAT_LABELS = {FORMAT_STORY: "история", FORMAT_DISASTER: "сериал-катастрофа"}
# Чем рисуется эпизод: картинки сцен (ИИ) или свой 3D-движок (лоу-поли, 0 ₽).
ENGINE_IMAGES, ENGINE_3D = "images", "3d"
ENGINES = (ENGINE_IMAGES, ENGINE_3D)
ENGINE_LABELS = {ENGINE_IMAGES: "картинки сцен (ИИ)", ENGINE_3D: "3D-движок (лоу-поли, 0 ₽)"}
# Чем рисуется 3D-серия: свой лоу-поли движок в сервисе или Blender на компьютере владельца (очередь хост-воркера).
RENDERER_THREE, RENDERER_BLENDER = "three", "blender"
RENDERERS = (RENDERER_THREE, RENDERER_BLENDER)
# Длина эпизода по жанру: (мин, макс, по умолчанию), секунды.
DURATIONS = {FORMAT_STORY: (30.0, 75.0, 50.0), FORMAT_DISASTER: (60.0, 90.0, 75.0)}


# Звуки — только настоящие записи: пак freesound, «Удары и шаги» Kenney и звуки, которые владелец добавил
# в «Файлы для монтажа» (add_sound_from_url, тег «звук»). Интерфейсные паки — «дёшево», их нет.
SFX_PACKS = ("freesound", "kenney-impact")
# Частые слова действий → слова, по которым ищется запись (русские основы и английские).
SFX_SYNONYMS = {
    "храп": "snore храп сон sleep", "будильник": "alarm clock будильник звон ring", "скреж": "scrape скрежет "
    "скребет scratch", "шаг": "footstep шаги steps", "удар": "impact удар hit punch", "смех": "laugh смех",
    "ах": "gasp ах удивление", "аплодисмент": "applause аплодисменты", "фейл": "sad trombone фейл провал",
    "деньг": "cash касса деньги cha-ching", "касс": "cash касса", "фото": "camera затвор", "вжух": "whoosh вжух",
    "бум": "boom бум драма", "напряж": "tension напряжение", "сирен": "alarm siren сирена тревога",
    "тревог": "alarm siren тревога", "двер": "door дверь", "стук": "knock стук", "поп": "pop поп",
}

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def ensure_stories_schema(conn) -> None:
    """Таблицы историй (из ``Database.init`` после ИИ-видео) + расширение этапа генерации."""
    for sql in STORY_SCHEMA_SQL:
        conn.execute(sql)
    for table, column, ddl in STORY_COLUMNS:
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _register()


# --------------------------------------------------------------------------- #
# Звуки на действиях: только настоящие записи
# --------------------------------------------------------------------------- #


def sfx_options(store: Any, project_id: int | None = None) -> list[dict]:
    """Доступные записи: паки ``SFX_PACKS`` и звуки владельца в «Файлах для монтажа» (тег «звук»): общие и файлы
    проекта ``project_id`` (app/asset_scope.py)."""
    from app.motion_assets import pack_items

    out: list[dict] = []
    for pack_id in SFX_PACKS:
        try:
            data = pack_items(pack_id, limit=500)
        except Exception:  # noqa: BLE001 - пака нет — звуков из него нет
            data = None
        if not data or data.get("kind") != "sfx":
            continue
        for item in data.get("items") or []:
            out.append({"ref": item["ref"], "label": str(item.get("name") or item["file"]),
                        "tags": [str(t) for t in item.get("tags") or []][:8], "source": pack_id,
                        "duration": item.get("duration") or item.get("duration_sec")})
    try:
        assets = store.list_montage_assets(visible_to=() if project_id is None else (int(project_id),))
    except Exception:  # noqa: BLE001
        assets = []
    for asset in assets:
        tags = str(asset.get("tags") or "").lower()
        if asset.get("kind") != "audio" or not ({"звук", "sfx"} & {t.strip() for t in tags.split(",")}):
            continue
        out.append({"ref": str(asset["id"]), "label": str(asset.get("label") or asset.get("original_filename") or ""),
                    "tags": [t.strip() for t in tags.split(",") if t.strip()][:8], "source": "library",
                    "duration": asset.get("duration_sec")})
    return out


def resolve_sfx(tag: Any, options: list[dict]) -> dict | None:
    """Тег звука из сценария («храп», «будильник», «sfx:freesound/punch.ogg», id файла) → запись."""
    text = " ".join(str(tag or "").split()).strip()
    if not text:
        return None
    exact = next((o for o in options if o["ref"] == text), None)
    if exact is not None:
        return exact
    low = text.lower().replace("ё", "е")
    extra = " ".join(v for k, v in SFX_SYNONYMS.items() if k in low)
    want = E.stems(f"{low} {extra}")
    if not want:
        return None
    best, best_score = None, 0.0
    for opt in options:
        have = E.stems(" ".join([opt["label"], " ".join(opt["tags"]), opt["ref"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                                 .replace("-", " ")]))
        hit = len(want & have)
        if not hit:
            continue
        score = hit + (0.5 if opt.get("source") == "library" else 0.0)  # своя запись владельца — точнее пака
        if score > best_score:
            best, best_score = opt, score
    return best


# --------------------------------------------------------------------------- #
# Эпизоды
# --------------------------------------------------------------------------- #

EP_JSON = {"script_json": ("script", {}), "beats_json": ("beats", []), "words_json": ("words", []),
           "timeline_json": ("timeline", {}), "options_json": ("options", {}), "notes_json": ("notes", [])}


def _episode_view(row: Any, full: bool = True) -> dict:
    data = dict(row)
    timeline = _loads(data.get("timeline_json"), {})
    for col, (key, default) in EP_JSON.items():
        raw = data.pop(col, None)
        if full or key in ("options", "notes"):
            data[key] = _loads(raw, default)
    data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
    wait = timeline.get("render3d") if data["status"] == "assembling" else None
    if isinstance(wait, dict) and wait.get("label"):
        data["status_label"] = str(wait["label"])     # «рендер на компьютере… 42%»
        data["render3d"] = wait
    data["editor_url"] = f"/clips/{int(data['clip_id'])}" if data.get("clip_id") else None
    return data


def get_episode(store: Any, episode_id: int, full: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM story_episodes WHERE id = ?", (int(episode_id),))
    if not row:
        raise KeyError(f"нет эпизода {episode_id}")
    return _episode_view(row, full)


def list_episodes(store: Any, project_id: int | None = None, world_id: int | None = None,
                  limit: int = 50) -> list[dict]:
    where, params = [], []
    if project_id is not None:
        where.append("project_id = ?")
        params.append(int(project_id))
    if world_id is not None:
        where.append("world_id = ?")
        params.append(int(world_id))
    sql = "SELECT * FROM story_episodes" + (" WHERE " + " AND ".join(where) if where else "")
    rows = store.db.query_all(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 500))))
    return [_episode_view(r, full=False) for r in rows]


def _sets(fields: dict) -> dict:
    sets: dict[str, Any] = {}
    for key, value in fields.items():
        col = next((c for c, (k, _d) in EP_JSON.items() if k == key), None)
        sets[col or key] = _json(value) if col else value
    return sets


def update_episode(store: Any, episode_id: int, **fields: Any) -> dict:
    sets = _sets(fields)
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_episodes SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(episode_id)))
    return get_episode(store, episode_id)


def set_status(store: Any, episode_id: int, status: str, expect: tuple[str, ...] | None = None, **fields: Any) -> bool:
    """Сменить статус (сравнить-и-записать: ``expect`` — из каких можно). False — эпизод уже в другом."""
    status = P._choice(status, STATUSES, "status")
    sets = {"status": status, **_sets(fields)}
    cols = ", ".join(f"{k} = ?" for k in sets)
    sql = f"UPDATE story_episodes SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    args: list[Any] = [*sets.values(), int(episode_id)]
    if expect:
        sql += f" AND status IN ({', '.join('?' for _ in expect)})"
        args += list(expect)
    return bool(store.db.execute(sql, args).rowcount)


def episode_format(world: dict, ep: dict | None = None) -> str:
    """Жанр эпизода: ``options.format`` эпизода, иначе формат мира (story | disaster)."""
    value = str(((ep or {}).get("options") or {}).get("format") or world.get("format") or FORMAT_STORY)
    return value if value in FORMATS else FORMAT_STORY


def episode_engine(ep: dict | None) -> str:
    """Чем рисуется эпизод: ``images`` — картинки сцен (ИИ, по карточке), ``3d`` — свой 3D-движок
    (app/series3d.py: лоу-поли сцена под озвучку, 0 ₽, без картинок сцен)."""
    value = str(((ep or {}).get("options") or {}).get("engine") or ENGINE_IMAGES)
    return value if value in ENGINES else ENGINE_IMAGES


def is_3d(ep: dict | None) -> bool:
    return episode_engine(ep) == ENGINE_3D


def create_episode(store: Any, world_id: int, premise: str, duration: Any = None, tone: str = "",
                   options: dict | None = None, requested_by: str = "agent", status: str = "new") -> dict:
    """Новый эпизод. ``status`` пишется сразу: черновик (``draft``) работник не увидит ни на миг. Жанр — ``options.format`` или формат мира; у сериала-катастрофы эпизод — часть
    сериала мира (``options.part``: следующая по счёту, если не задана), длина 60–90 с. ``options.engine``:
    ``3d`` — серия на своём 3D-движке (без картинок сцен, 0 ₽), иначе картинки сцен; ``options.renderer`` у 3D:
    ``three`` — лоу-поли движок в сервисе, ``blender`` — кино-качество Blender на компьютере владельца (очередь
    хост-воркера, app/render_queue.py)."""
    world = get_world(store, world_id)
    options = dict(options or {})
    fmt = P._choice(str(options.get("format") or world.get("format") or FORMAT_STORY), FORMATS, "format")
    options["format"] = fmt
    if options.get("engine") in (None, ""):
        options.pop("engine", None)
    else:
        options["engine"] = P._choice(str(options["engine"]), ENGINES, "engine")
    if options.get("renderer") in (None, ""):
        options.pop("renderer", None)
    else:
        options["renderer"] = P._choice(str(options["renderer"]), RENDERERS, "renderer")
        if options["renderer"] == RENDERER_BLENDER:
            options["engine"] = ENGINE_3D
    if options.get("quality") in (None, ""):
        options.pop("quality", None)
    else:
        from app.series3d_blender import QUALITY

        options["quality"] = P._choice(str(options["quality"]), tuple(QUALITY), "quality")
    lo, hi, default = DURATIONS[fmt]
    label = FORMAT_LABELS[fmt]
    premise = P._long_text(premise, 800)
    if len(premise) < 3:
        raise StoryError("нужна завязка истории (premise): о чём, кто, где, чем кончится")
    try:
        seconds = float(duration or default)
    except (TypeError, ValueError) as exc:
        raise StoryError(f"duration — секунды {lo:.0f}–{hi:.0f}") from exc
    if not lo <= seconds <= hi:
        raise StoryError(f"duration {seconds:g} с: {label} {lo:.0f}–{hi:.0f} с")
    if fmt == FORMAT_DISASTER:
        from app import stories_disaster as D

        options = D.episode_options(store, world, options)
    cur = store.db.execute(
        "INSERT INTO story_episodes (project_id, world_id, premise, duration_target, tone, options_json, requested_by, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(world["project_id"]), int(world_id), premise, round(seconds, 1),
         P._text(tone, 60) or ("напряжённо" if fmt == FORMAT_DISASTER else TONES[0]),
         _json(options), P._text(requested_by, 40), P._choice(status, STATUSES, "status")))
    return get_episode(store, int(cur.lastrowid))


def delete_episode(store: Any, episode_id: int) -> dict:
    from app import aivideo_gen as G

    ep = get_episode(store, episode_id, full=False)
    if ep.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'эпизод удалён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(ep["approval_id"]),))
    _drop_refs(store, int(ep["project_id"]), T_EP, int(episode_id))
    store.db.execute("DELETE FROM story_episodes WHERE id = ?", (int(episode_id),))
    G.kick()
    return {"ok": True, "deleted": int(episode_id), "note": "клип (если собран) остался — удаляется отдельно"}


def episode_group(episode_id: int) -> str:
    return f"{T_EP}:{int(episode_id)}"


def narration_text(ep: dict) -> str:
    return str((ep.get("script") or {}).get("text") or "").strip()


def narration_ref(store: Any, ep: dict) -> dict | None:
    """Готовая озвучка эпизода тем же голосом и тем же текстом (иначе — нет)."""
    from app.aivideo_gen import _asset_path, _fresh, _line_key

    voice = world_voice(store, get_world(store, int(ep["world_id"])), is_dry(ep))
    ref = V.selected_ref(store, int(ep["project_id"]), T_EP, int(ep["id"]), NARRATION_KIND)
    if not _fresh(ref) or ref["prompt"] != _line_key(voice, narration_text(ep)):
        return None
    return ref if _asset_path(store, ref) else None


def beat_scene_ids(ep: dict) -> list[int]:
    return [int(b["scene_id"]) for b in ep.get("beats") or [] if b.get("scene_id")]


def overview(store: Any, project_id: int) -> dict:
    """Вкладка «Истории»: миры с актёрами, локациями и библиотекой сцен, эпизоды, бюджет, карточки."""
    from app import aivideo_gen as G

    project = V.require_project(store, project_id)
    worlds = []
    for w in list_worlds(store, project_id):
        w["characters"] = list_characters(store, int(w["id"]))
        w["locations"] = list_locations(store, int(w["id"]))
        w["scenes"] = list_scenes(store, int(w["id"]))
        worlds.append(w)
    return {"project": {k: project.get(k) for k in ("id", "name", "kind")},
            "provider": V._style_row(store, project_id)["provider"],
            "worlds": worlds, "episodes": list_episodes(store, project_id),
            "budget": G.budget_view(store, project_id), "episode_cap_rub": float(settings.stories_episode_cap_rub),
            "approvals": [a for a in G.stage_approvals(store, project_id) if a.get("stage") == STAGE]}


# --------------------------------------------------------------------------- #
# Расширение этапа генерации (app/aivideo_gen.py): сцены, листы, плиты, кадр стиля, озвучка
# --------------------------------------------------------------------------- #

NO_TEXT = ("No text, no letters, no written words, no captions, no subtitles, no watermark, no logo (cartoon "
           "symbols named in the accent are fine).")
STYLE_PROMPT = (
    "{style}. Style reference frame for an animated narrated story series «{name}»: {about}. A cosy miniature "
    "3D set with one simple round-headed character in the middle, showing the world's materials, light and color "
    "mood. Vertical 9:16. {no_text}{neg}")
SHEET_PROMPT = (
    "{style}. Character model sheet of an original simple cartoon character '{name}': {identity}. Neutral light-grey "
    "background, top row — front, three-quarter, side and back views, full body; bottom row — four expressions "
    "(neutral, happy, surprised, sly), half body. The same character in every view, consistent proportions, "
    "materials and colors; match the style reference images. {no_text}{neg}")
PLATE_PROMPT = (
    "{style}. Empty background plate of a location, NO characters and no people, vertical 9:16 composition, eye-level "
    "camera, the set fills the frame. Location «{name}»: {description}. Match the style reference images. "
    "{no_text}{neg}")
SCENE_PROMPT = (
    "{style}. A still frame from an animated narrated story, vertical 9:16. {where} {who} Action: {action}.{accent} "
    "Characters must look exactly like in their reference character sheets (same head shape, colors, clothes); the "
    "set must match the location plate reference; the whole frame matches the style reference images. Keep the "
    "characters and the action in the upper two thirds of the frame, the lower third calm (subtitles go there). "
    "Physically plausible: characters stand or sit on the floor or furniture. {no_text}{neg}")


def _style_args(world: dict) -> dict:
    neg = f" Avoid: {world['negative'].strip().rstrip('.')}." if world.get("negative") else ""
    return {"style": (world.get("style_passport") or DEFAULT_STYLE).strip().rstrip("."), "neg": neg,
            "no_text": NO_TEXT, "name": world["name"]}


def scene_prompt(store: Any, world: dict, scene: dict) -> tuple[str, list[Path], list[str], list[str]]:
    """(промпт, референсы, роли референсов, чего не хватило) сцены."""
    from app.aivideo_gen import _asset_path

    refs: list[Path] = []
    roles: list[str] = []
    missing: list[str] = []
    where = "Generic cosy room."
    if scene.get("location_id"):
        loc = get_location(store, int(scene["location_id"]))
        where = f"Location «{loc['name']}»: {loc['description'].strip().rstrip('.')}."
        plate = plate_path(store, loc)
        if plate:
            refs.append(plate)
            roles.append(f"плита: {loc['name']}")
        else:
            missing.append(f"плита «{loc['name']}»")
    who_parts = []
    for cid in _cast(scene.get("cast")):
        c = get_character(store, cid)
        who_parts.append(f"'{c['name']}': {c['identity_card'].strip().rstrip('.')}")
        sheet = sheet_path(store, c)
        if sheet:
            refs.append(sheet)
            roles.append(f"лист: {c['name']}")
        else:
            missing.append(f"лист «{c['name']}»")
    who = ("Characters: " + "; ".join(who_parts) + ".") if who_parts else "No characters in frame."
    for ref in style_refs(store, world)[:2]:
        path = _asset_path(store, ref)
        if path:
            refs.append(path)
            roles.append("стиль")
    accent = f" Small cartoon accent: {scene['accent'].strip().rstrip('.')}." if scene.get("accent") else ""
    if world.get("format") == FORMAT_DISASTER:
        # Сериал-катастрофа: цифры HUD — в верхнем левом углу, лицо героя туда не ставим.
        accent += (" Keep the top-left corner of the frame calm and free of faces (game HUD numbers go there); "
                   "close camera, often over the hero's shoulder.")
    prompt = SCENE_PROMPT.format(**_style_args(world), where=where, who=who,
                                 action=str(scene.get("action") or "").strip().rstrip("."), accent=accent)
    return prompt, refs, roles, missing


class _Extension:
    stage = STAGE
    label = "сцены, листы актёров, плиты и озвучка истории"
    card_prefix = "История"
    cap_hint = "меньше новых сцен (переиспользуй библиотеку) или раздели (items)"
    target_types = (T_WORLD, T_CHAR, T_LOC, T_SCENE, T_EP)
    ref_kinds = {STYLE_KIND: "кадр стиля истории", SHEET_KIND: "лист актёра", PLATE_KIND: "плита локации",
                 SCENE_KIND: "сцена истории", NARRATION_KIND: "озвучка истории"}
    aspects = {STYLE_KIND: "9:16", SHEET_KIND: "16:9", PLATE_KIND: "9:16", SCENE_KIND: "9:16"}

    @property
    def card_cap_rub(self) -> float:
        return float(settings.stories_episode_cap_rub)

    def project_of(self, store: Any, target_type: str, target_id: int) -> int:
        if target_type == T_WORLD:
            return int(get_world(store, target_id)["project_id"])
        if target_type == T_CHAR:
            return int(get_character(store, target_id)["project_id"])
        if target_type == T_LOC:
            return int(get_location(store, target_id)["project_id"])
        if target_type == T_SCENE:
            return int(get_scene(store, target_id)["project_id"])
        return int(get_episode(store, target_id, full=False)["project_id"])

    # --- план ---------------------------------------------------------------- #

    def plan(self, store: Any, project_id: int, items: list, regenerate: bool) -> list[dict]:
        from app import aivideo_gen as G

        if not items:
            raise StoryError("этап историй — только с items: «story_ep:ID», «story_scene:ID», «story_char:ID», "
                             "«story_loc:ID», «story_world:ID»")
        out: list[dict] = []
        for raw in items:
            target_type, target_id, kind, _slot = G._parse_item(raw)
            if target_type == T_EP:
                out += self._episode_items(store, project_id, target_id, kind, regenerate)
                continue
            owner = self.project_of(store, target_type, target_id)
            if int(owner) != int(project_id):
                raise StoryError(f"{target_type}:{target_id} из другого проекта")
            if target_type == T_SCENE:
                scene = get_scene(store, target_id)
                if scene["ready"] and not regenerate:
                    continue
                out.append(self._scene_item(store, project_id, scene, ""))
            elif target_type == T_CHAR:
                c = get_character(store, target_id)
                if c["sheet"] and not regenerate:
                    continue
                out.append(self._image(store, project_id, T_CHAR, int(c["id"]), SHEET_KIND, f"лист актёра «{c['name']}»",
                                       ""))
            elif target_type == T_LOC:
                loc = get_location(store, target_id)
                if loc["plate"] and not regenerate:
                    continue
                out.append(self._image(store, project_id, T_LOC, int(loc["id"]), PLATE_KIND,
                                       f"плита «{loc['name']}»", ""))
            elif target_type == T_WORLD:
                world = get_world(store, target_id)
                if world["style_refs"] and not regenerate:
                    continue
                out.append(self._image(store, project_id, T_WORLD, int(world["id"]), STYLE_KIND,
                                       f"кадр стиля «{world['name']}»", "", slot="1"))
            else:
                raise StoryError(f"элемент {raw!r}: «story_ep:ID[:story_narration|story_scene]», «story_scene:ID», "
                                 "«story_char:ID», «story_loc:ID» или «story_world:ID»")
        return out

    def _episode_items(self, store: Any, project_id: int, episode_id: int, kind: str, regenerate: bool) -> list[dict]:
        ep = get_episode(store, episode_id)
        if int(ep["project_id"]) != int(project_id):
            raise StoryError("эпизод из другого проекта")
        group = episode_group(episode_id)
        out: list[dict] = []
        if kind in ("", NARRATION_KIND):
            item = self._narration_item(store, ep, regenerate)
            if item is not None:
                out.append({**item, "group": group})
        if kind not in ("", SCENE_KIND):
            return out
        world = get_world(store, int(ep["world_id"]))
        new_scenes = []
        seen: set[int] = set()
        dry = is_dry(ep)
        for sid in beat_scene_ids(ep):
            if sid in seen:
                continue
            seen.add(sid)
            scene = get_scene(store, sid)
            if not scene_usable(scene, dry):
                new_scenes.append(scene)
        if not new_scenes:
            return out
        # Основа новых сцен: кадр стиля мира (нет ни одного референса стиля), листы и плиты — до сцен,
        # в той же карточке (работник ждёт их: deps).
        if not world["style_refs"]:
            out.append(self._image(store, project_id, T_WORLD, int(world["id"]), STYLE_KIND,
                                   f"кадр стиля «{world['name']}»", group, slot="1", dry=dry))
        chars, locs = [], []
        for scene in new_scenes:
            for cid in _cast(scene["cast"]):
                if cid not in chars:
                    chars.append(cid)
            if scene.get("location_id") and int(scene["location_id"]) not in locs:
                locs.append(int(scene["location_id"]))
        for cid in chars:
            c = get_character(store, cid)
            if not c["sheet"]:
                out.append(self._image(store, project_id, T_CHAR, cid, SHEET_KIND, f"лист актёра «{c['name']}»", group, dry=dry))
        for lid in locs:
            loc = get_location(store, lid)
            if not loc["plate"]:
                out.append(self._image(store, project_id, T_LOC, lid, PLATE_KIND, f"плита «{loc['name']}»", group, dry=dry))
        out += [self._scene_item(store, project_id, scene, group, dry) for scene in new_scenes]
        return out

    def _image(self, store: Any, project_id: int, target_type: str, target_id: int, kind: str, label: str,
               group: str, slot: str = "", dry: bool = False) -> dict:
        from app import aivideo_gen as G

        style = V.get_style(store, project_id)
        # Заглушка — только в черновике. Провайдер none (значение по умолчанию у любого проекта) — не повод выдать
        # заглушку за сцену: настоящая картинка платная, значит карточка владельцу с суммой (как у объяснялок).
        provider = "none" if dry else (style["provider"] if style["provider"] != "none" else E.DRAW_PROVIDER)
        item = G._image_item(STAGE, target_type, target_id, kind, label, style["image_model"], style["quality"],
                             provider, slot)
        item["aspect"] = self.aspects.get(kind, "9:16")
        return {**item, "group": group} if group else item

    def _scene_item(self, store: Any, project_id: int, scene: dict, group: str, dry: bool = False) -> dict:
        title = scene["title"] or scene["action"]
        return self._image(store, project_id, T_SCENE, int(scene["id"]), SCENE_KIND, f"сцена «{P._text(title, 60)}»",
                           group, dry=dry)

    def _narration_item(self, store: Any, ep: dict, regenerate: bool) -> dict | None:
        from app.aivideo_providers import get_tts

        text = narration_text(ep)
        if not text:
            raise StoryError(f"у эпизода {ep['id']} нет текста — сначала сценарий")
        if not regenerate and narration_ref(store, ep) is not None:
            return None
        voice = world_voice(store, get_world(store, int(ep["world_id"])), is_dry(ep))
        tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style"))
        price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
        note = {"gemini": "Gemini TTS, бесплатно", "none": "сухой прогон"}.get(voice["provider"],
                                                                              "цена TTS из app/dubbing.py")
        dialog: dict = {}
        if is_3d(ep) and voice["provider"] == "gemini":
            from app.dubbing import hero_voice_for
            from app.series3d_blender import dialog_lines, has_hero_lines, hero_names

            sentences = [x for x in (ep.get("script") or {}).get("sentences") or [] if str(x.get("text") or "").strip()]
            names = hero_names(ep.get("script"))
            if has_hero_lines(sentences, names):   # прямая речь героя — другим голосом, рассказчик — своим
                dialog = {"dialog": dialog_lines(sentences, names), "hero_voice": hero_voice_for(voice["voice"])}
        return {"stage": STAGE, "kind": "tts", "target_type": T_EP, "target_id": int(ep["id"]),
                "ref_kind": NARRATION_KIND, "slot": "", "label": f"озвучка «{P._text(ep['title'] or ep['premise'], 60)}»",
                "provider": voice["provider"], "model": voice["model"], "voice": voice["voice"],
                **({"style": voice["style"]} if voice.get("style") else {}), **dialog,
                "text": text, "chars": len(text), "estimate_rub": round(len(text) * price, 2), "price_note": note,
                "regenerate": bool(regenerate)}

    # --- запуск -------------------------------------------------------------- #

    def deps(self, store: Any, job: dict) -> list[tuple[int, str]]:
        """Что должно быть готово раньше (в той же партии): сцене — листы, плита, кадр стиля; листу и
        плите — кадр стиля."""
        kind = job.get("ref_kind")
        if kind == SCENE_KIND:
            scene = get_scene(store, int(job["target_id"]))
            out = [(cid, f"{T_CHAR}:{SHEET_KIND}") for cid in _cast(scene["cast"])]
            if scene.get("location_id"):
                out.append((int(scene["location_id"]), f"{T_LOC}:{PLATE_KIND}"))
            return out + [(int(scene["world_id"]), f"{T_WORLD}:{STYLE_KIND}")]
        if kind == SHEET_KIND:
            return [(int(get_character(store, int(job["target_id"]))["world_id"]), f"{T_WORLD}:{STYLE_KIND}")]
        if kind == PLATE_KIND:
            return [(int(get_location(store, int(job["target_id"]))["world_id"]), f"{T_WORLD}:{STYLE_KIND}")]
        return []

    def build(self, store: Any, job: dict) -> tuple[str, list[Path], list[str], dict]:
        from app.aivideo_gen import _asset_path

        kind, target_id = job["ref_kind"], int(job["target_id"])
        if kind == SCENE_KIND:
            scene = get_scene(store, target_id)
            world = get_world(store, int(scene["world_id"]))
            prompt, refs, roles, missing = scene_prompt(store, world, scene)
            return prompt, refs, ["сцена", world["name"], scene["title"] or scene["slug"]], {"roles": roles,
                                                                                            "missing": missing}
        if kind == SHEET_KIND:
            c = get_character(store, target_id)
            world = get_world(store, int(c["world_id"]))
            styles = [p for p in (_asset_path(store, r) for r in style_refs(store, world)[:2]) if p]
            prompt = SHEET_PROMPT.format(**_style_args(world) | {"name": c["name"]},
                                         identity=c["identity_card"].strip().rstrip("."))
            return prompt, styles, ["лист актёра", c["name"]], {"roles": ["стиль"] * len(styles), "missing": []}
        if kind == PLATE_KIND:
            loc = get_location(store, target_id)
            world = get_world(store, int(loc["world_id"]))
            styles = [p for p in (_asset_path(store, r) for r in style_refs(store, world)[:2]) if p]
            prompt = PLATE_PROMPT.format(**_style_args(world) | {"name": loc["name"]},
                                         description=loc["description"].strip().rstrip("."))
            return prompt, styles, ["плита", loc["name"]], {"roles": ["стиль"] * len(styles), "missing": []}
        if kind == STYLE_KIND:
            world = get_world(store, target_id)
            project = P.get_project(store, int(world["project_id"]), include_sources=False)
            about = world.get("style_label") or project.get("description") or project["name"]
            prompt = STYLE_PROMPT.format(**_style_args(world), about=about)
            uploads = [p for p in (_asset_path(store, r) for r in V.list_refs(
                store, int(world["project_id"]), T_WORLD, target_id, "upload")) if p][-3:]
            return prompt, uploads, ["кадр стиля", world["name"]], {"roles": [p.name for p in uploads], "missing": []}
        raise StoryError(f"неизвестный вид картинки истории {kind}")

    def card_line(self, store: Any, project_id: int, est: dict) -> str:
        groups = {i.get("group") for i in est["items"] if i.get("group")}
        count = {k: [i for i in est["items"] if i["ref_kind"] == k] for k in self.ref_kinds}
        names = {SCENE_KIND: "новых сцен", SHEET_KIND: "листов актёров", PLATE_KIND: "плит локаций",
                 STYLE_KIND: "кадр стиля"}
        parts = [f"{names[k]} {len(v)} ≈ {sum(i['estimate_rub'] for i in v):.2f} ₽" for k, v in count.items()
                 if v and k in names]
        tts = count[NARRATION_KIND]
        if tts:
            parts.append(f"озвучка {tts[0]['chars']} симв. ({tts[0]['model']}, {tts[0]['voice']}) ≈ "
                         f"{sum(i['estimate_rub'] for i in tts):.2f} ₽")
        head = "История"
        if len(groups) == 1:
            try:
                ep = get_episode(store, int(next(iter(groups)).split(":")[1]))
                world = get_world(store, int(ep["world_id"]))
                scenes = set(beat_scene_ids(ep))
                reused = len(scenes) - len(count[SCENE_KIND])
                head = (f"История «{P._text(ep['title'] or ep['premise'], 80)}» (мир {world['name']}, "
                        f"{ep['duration_target']:.0f} с; кадров {len(ep.get('beats') or [])}, сцен из библиотеки: "
                        f"{max(0, reused)})")
            except (KeyError, ValueError, IndexError):
                pass
        return f"{head}: " + "; ".join(parts) + f" — итого ≈ {est['total_rub']:.2f} ₽."


_REGISTERED = False


def _register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from app import aivideo_gen as G

    ext = _Extension()
    G.register_extension(ext)
    G.REF_ASPECT.update(ext.aspects)
    _REGISTERED = True


def request_item(store: Any, target: str, regenerate: bool = False, requested_by: str = "agent",
                 note: str = "") -> dict:
    """Нарисовать (нет картинки) или перерисовать (``regenerate``) одну цель: своя маленькая карточка
    (сухой прогон — сразу). ``target`` — «story_scene:ID», «story_char:ID», «story_loc:ID», «story_world:ID»."""
    from app import aivideo_gen as G

    target_type, target_id, _kind, _slot = G._parse_item(target)
    if target_type not in (T_SCENE, T_CHAR, T_LOC, T_WORLD):
        raise StoryError("цель: story_scene:ID, story_char:ID, story_loc:ID или story_world:ID")
    pid = _Extension().project_of(store, target_type, target_id)
    return G.request_stage(store, int(pid), STAGE, None, [f"{target_type}:{int(target_id)}"], regenerate=regenerate,
                           requested_by=requested_by, note=note)


def episode_renderer(ep: dict | None) -> str:
    """3D-серия: ``three`` — лоу-поли движок сервиса, ``blender`` — Blender на компьютере владельца."""
    value = str(((ep or {}).get("options") or {}).get("renderer") or RENDERER_THREE)
    return value if value in RENDERERS else RENDERER_THREE
