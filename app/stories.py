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
сухой прогон (провайдер проекта ``none``) — сразу, 0 ₽.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import explainer as E
from app import projects as P
from app.settings import settings

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
STYLE_KIND = "story_style"
SHEET_KIND = "story_sheet"
PLATE_KIND = "story_plate"
SCENE_KIND = "story_scene"
NARRATION_KIND = "story_narration"
T_WORLD, T_CHAR, T_LOC, T_SCENE, T_EP = "story_world", "story_char", "story_loc", "story_scene", "story_ep"
STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling", "done", "failed",
            "cancelled")
ACTIVE_STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling")
STATUS_LABELS = {"new": "пишу сценарий", "scripted": "подбираю сцены", "planned": "считаю стоимость",
                 "awaiting_approval": "ждёт одобрения", "generating": "рисую сцены и озвучку",
                 "assembling": "собираю клип", "done": "готово", "failed": "ошибка", "cancelled": "отменено"}
TONES = ("смешно", "с иронией", "абсурдно", "уютно", "драматично", "напряжённо")
DURATION_MIN = 30.0
DURATION_MAX = 75.0
ORIGINS = ("generated", "upload", "agent")
REUSE_MIN = 0.5  # Жаккар основ «теги + действие + акцент»: сцена ≈ готовой → берём готовую
MAX_TAGS = 16
STYLE_REFS_MAX = 3
DEFAULT_STYLE_LABEL = "3D-игрушки: круглые головы, мягкий пластилиновый свет"
DEFAULT_STYLE = ("Cute 3D toy-like animation, simple characters with big perfectly round heads, dot eyes and tiny "
                 "bodies, soft matte clay/plasticine materials, warm soft studio lighting, cosy miniature diorama "
                 "sets, gentle depth of field, soft saturated colors")
FORMAT_STORY, FORMAT_DISASTER = "story", "disaster"
FORMATS = (FORMAT_STORY, FORMAT_DISASTER)
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
DEFAULT_CAPTIONS = {"color": "#ffffff", "active": "#ffffff", "stroke": "#151515", "size": 110, "y": 0.74,
                    "uppercase": False}
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


class StoryError(V.AivError):
    """Понятная ошибка историй (нет мира, неверный актёр, эпизод не в том состоянии)."""


def ensure_stories_schema(conn) -> None:
    """Таблицы историй (из ``Database.init`` после ИИ-видео) + расширение этапа генерации."""
    for sql in STORY_SCHEMA_SQL:
        conn.execute(sql)
    for table, column, ddl in STORY_COLUMNS:
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _register()


def _loads(text: Any, default: Any) -> Any:
    return V._loads(text, default)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tags(value: Any) -> list[str]:
    return E._tags(value)


def _drop_refs(store: Any, project_id: int, target_type: str, target_id: int) -> None:
    store.db.execute("DELETE FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ?",
                     (int(project_id), target_type, int(target_id)))


def _ref_brief(ref: dict | None) -> dict | None:
    if not ref or not ref.get("asset_id"):
        return None
    return {"ref_id": ref["id"], "asset_id": ref["asset_id"], "url": ref["url"], "version": ref["version"],
            "stale": ref["stale"]}


def _image_asset(store: Any, asset_id: Any, what: str, project_id: int | None = None) -> dict:
    from app.asset_scope import allowed, refusal

    asset = store.get_montage_asset(int(asset_id))
    if asset.get("kind") != "image":
        raise StoryError(f"{what} — картинка (png/jpg/webp)")
    if project_id is not None and not allowed(asset, project_id):
        raise StoryError(f"{what}: " + refusal(store, asset, project_id, "мир"))
    return asset


# --------------------------------------------------------------------------- #
# Мир: стиль, рассказчик, голос, музыка, субтитры
# --------------------------------------------------------------------------- #

WORLD_FIELDS = ("name", "style_label", "style_passport", "negative", "narrator", "voice", "music_track_id",
                "captions", "notes", "slug", "format", "music")
MUSIC_MODES = ("", "auto", "none")


def _captions(value: Any, current: dict | None = None) -> dict:
    out = {**DEFAULT_CAPTIONS, **(current or {})}
    if not isinstance(value, dict):
        return out
    for key in ("color", "active", "stroke"):
        if value.get(key):
            found = V._hexes([value[key]])
            if not found:
                raise StoryError(f"captions.{key}: цвет #rrggbb")
            out[key] = found[0]
    if value.get("size") is not None:
        out["size"] = max(48, min(200, int(float(value["size"]))))
    if value.get("y") is not None:
        out["y"] = round(max(0.55, min(0.86, float(value["y"]))), 3)
    if value.get("uppercase") is not None:
        out["uppercase"] = bool(value["uppercase"])
    return out


def _world_view(store: Any, row: Any) -> dict:
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
    data["captions"] = {**DEFAULT_CAPTIONS, **_loads(data.pop("captions_json", "{}"), {})}
    data["style_refs"] = [b for b in (_ref_brief(r) for r in style_refs(store, data)) if b]
    return data


def style_refs(store: Any, world: dict) -> list[dict]:
    """Референсы стиля мира: выбранные версии слотов 1..3 (загрузка или нарисованный кадр стиля)."""
    out = []
    for slot in range(1, STYLE_REFS_MAX + 1):
        ref = V.selected_ref(store, int(world["project_id"]), T_WORLD, int(world["id"]), STYLE_KIND, str(slot))
        if ref and ref.get("asset_id"):
            out.append(ref)
    return out


def get_world(store: Any, world_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM story_worlds WHERE id = ?", (int(world_id),))
    if not row:
        raise KeyError(f"нет мира историй {world_id}")
    return _world_view(store, row)


def list_worlds(store: Any, project_id: int | None = None) -> list[dict]:
    if project_id is None:
        rows = store.db.query_all("SELECT * FROM story_worlds ORDER BY id")
    else:
        rows = store.db.query_all("SELECT * FROM story_worlds WHERE project_id = ? ORDER BY id", (int(project_id),))
    return [_world_view(store, r) for r in rows]


def save_world(store: Any, project_id: int | None = None, world_id: int | None = None, **fields: Any) -> dict:
    """Новый мир (``project_id`` проекта «ИИ-видео») или правка (``world_id``). Смена стиля не
    перерисовывает готовое: сцены, листы и плиты помечаются «устарели» (видно в библиотеке)."""
    current = get_world(store, int(world_id)) if world_id else None
    if current is None:
        if project_id is None:
            raise StoryError("нужен project_id проекта «ИИ-видео» (или world_id для правки)")
        V.require_project(store, int(project_id))
    sets: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in WORLD_FIELDS or value is None:
            continue
        if key == "name":
            sets[key] = P._text(value, 60)
        elif key == "style_label":
            sets[key] = P._text(value, 120)
        elif key in ("style_passport", "negative"):
            sets[key] = P._long_text(value, 800)
        elif key in ("narrator", "notes"):
            sets[key] = P._long_text(value, 600)
        elif key == "voice":
            sets["voice_json"] = _json(E._voice(value))
        elif key == "music_track_id":
            sets[key] = int(value) if value not in ("", 0, "0") else None
            if sets[key]:
                try:
                    store.get_audio_track(sets[key])
                except KeyError as exc:
                    raise StoryError(f"нет трека {value} — list_music") from exc
        elif key == "captions":
            sets["captions_json"] = _json(_captions(value, (current or {}).get("captions")))
        elif key == "slug":
            sets[key] = V.slugify(value, "world")
        elif key == "format":
            sets[key] = P._choice(str(value or "").strip() or FORMAT_STORY, FORMATS, "format")
        elif key == "music":
            sets[key] = P._choice(str(value or "").strip().lower(), MUSIC_MODES, "music")
    if current is None and sets.get("format") == FORMAT_DISASTER:
        # Сериал-катастрофа: лоу-поли и тёмный свет, рассказчик на «вы» → 3-е лицо, музыку не вшиваем.
        from app import stories_disaster as D

        for key, default in (("style_label", D.STYLE_LABEL), ("style_passport", D.STYLE), ("narrator", D.NARRATOR),
                             ("music", "none")):
            if not sets.get(key):
                sets[key] = default
    if current is None:
        pid = int(project_id)
        name = sets.get("name") or "Истории"
        slug = V._unique_slug(store, "story_worlds", "project_id", pid,
                              sets.pop("slug", "") or V.slugify(name, "world"), None)
        cur = store.db.execute("INSERT INTO story_worlds (project_id, slug, name, style_label, style_passport, "
                               "voice_json, captions_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (pid, slug, name, DEFAULT_STYLE_LABEL, DEFAULT_STYLE,
                                _json({"provider": "gemini"}), _json(DEFAULT_CAPTIONS)))
        world_id = int(cur.lastrowid)
    elif "slug" in sets:
        sets["slug"] = V._unique_slug(store, "story_worlds", "project_id", int(current["project_id"]), sets["slug"],
                                      int(current["id"]))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_worlds SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(world_id)))
    if current is not None and any(k in sets and sets[k] != current.get(k) for k in ("style_passport", "negative")):
        mark_world_stale(store, get_world(store, int(world_id)))
    return get_world(store, int(world_id))


def mark_world_stale(store: Any, world: dict) -> int:
    """Стиль мира поменялся — его картинки «устарели» (ничего не перерисовывается само)."""
    n = 0
    pid = int(world["project_id"])
    for table, target in (("story_characters", T_CHAR), ("story_locations", T_LOC), ("story_scenes", T_SCENE)):
        ids = [int(r["id"]) for r in store.db.query_all(f"SELECT id FROM {table} WHERE world_id = ?",
                                                        (int(world["id"]),))]
        for tid in ids:
            n += int(V.mark_stale(store, pid, target, tid) or 0)
    return n


def delete_world(store: Any, world_id: int) -> dict:
    world = get_world(store, world_id)
    pid = int(world["project_id"])
    busy = [int(r["id"]) for r in store.db.query_all(
        f"SELECT id FROM story_episodes WHERE world_id = ? AND status IN ({', '.join('?' for _ in ACTIVE_STATUSES)})",
        (int(world_id), *ACTIVE_STATUSES))]
    if busy:
        raise StoryError(f"у мира эпизоды в работе {busy} — отмени их (story_cancel)")
    for table, target in (("story_characters", T_CHAR), ("story_locations", T_LOC), ("story_scenes", T_SCENE),
                          ("story_episodes", T_EP)):
        for r in store.db.query_all(f"SELECT id FROM {table} WHERE world_id = ?", (int(world_id),)):
            _drop_refs(store, pid, target, int(r["id"]))
    _drop_refs(store, pid, T_WORLD, int(world_id))
    store.db.execute("DELETE FROM story_worlds WHERE id = ?", (int(world_id),))
    return {"ok": True, "deleted": int(world_id), "note": "картинки остались в «Файлах для монтажа», клипы целы"}


def add_style_ref(store: Any, world_id: int, asset_id: int) -> dict:
    """Референс стиля — картинка владельца (в свободный слот, иначе заменяет третий)."""
    world = get_world(store, world_id)
    asset = _image_asset(store, asset_id, "референс стиля", int(world["project_id"]))
    taken = {str(r["slot"]) for r in style_refs(store, world)}
    slot = next((str(n) for n in range(1, STYLE_REFS_MAX + 1) if str(n) not in taken), str(STYLE_REFS_MAX))
    V.add_ref(store, int(world["project_id"]), T_WORLD, int(world_id), STYLE_KIND, int(asset["id"]),
              prompt="upload", slot=slot)
    return get_world(store, world_id)


def world_voice(store: Any, world: dict) -> dict:
    """Голос рассказчика: провайдер мира; проект в сухом прогоне (провайдер ``none``) — тон."""
    return E.mascot_voice(store, {"project_id": world["project_id"], "voice": world.get("voice") or {}})


# --------------------------------------------------------------------------- #
# Актёры и локации
# --------------------------------------------------------------------------- #


def _char_view(store: Any, row: Any, project_id: int) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["sheet"] = _ref_brief(V.selected_ref(store, int(project_id), T_CHAR, int(data["id"]), SHEET_KIND))
    return data


def _loc_view(store: Any, row: Any, project_id: int) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["plate"] = _ref_brief(V.selected_ref(store, int(project_id), T_LOC, int(data["id"]), PLATE_KIND))
    return data


def get_character(store: Any, character_id: int) -> dict:
    row = store.db.query_one("SELECT c.*, w.project_id FROM story_characters c JOIN story_worlds w ON w.id = c.world_id "
                             "WHERE c.id = ?", (int(character_id),))
    if not row:
        raise KeyError(f"нет актёра {character_id}")
    return _char_view(store, row, int(row["project_id"]))


def list_characters(store: Any, world_id: int) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT c.*, w.project_id FROM story_characters c JOIN story_worlds w ON w.id = c.world_id "
                              "WHERE c.world_id = ? ORDER BY c.id", (int(world_id),))
    return [_char_view(store, r, int(world["project_id"])) for r in rows]


def get_location(store: Any, location_id: int) -> dict:
    row = store.db.query_one("SELECT l.*, w.project_id FROM story_locations l JOIN story_worlds w ON w.id = l.world_id "
                             "WHERE l.id = ?", (int(location_id),))
    if not row:
        raise KeyError(f"нет локации {location_id}")
    return _loc_view(store, row, int(row["project_id"]))


def list_locations(store: Any, world_id: int) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT l.*, w.project_id FROM story_locations l JOIN story_worlds w ON w.id = l.world_id "
                              "WHERE l.world_id = ? ORDER BY l.id", (int(world_id),))
    return [_loc_view(store, r, int(world["project_id"])) for r in rows]


def save_character(store: Any, world_id: int | None = None, character_id: int | None = None, name: Any = None,
                   identity_card: Any = None, persona: Any = None, tags: Any = None, slug: Any = None,
                   sheet_asset_id: Any = None, origin: str = "agent") -> dict:
    """Актёр мира: новый (``world_id``) или правка (``character_id``). ``identity_card`` — ПО-АНГЛИЙСКИ
    (форма головы, цвета hex, одежда, деталь) — дословно в каждый промпт сцены с ним."""
    if character_id:
        current = get_character(store, int(character_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или character_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(name):
            raise StoryError("нужно имя актёра (name)")
        if not P._text(identity_card):
            raise StoryError("нужна карта актёра (identity_card) ПО-АНГЛИЙСКИ: форма, цвета, одежда, деталь — "
                             "она идёт в каждый промпт сцены дословно")
    sets: dict[str, Any] = {}
    if name is not None:
        sets["name"] = P._text(name, 60)
    if identity_card is not None:
        sets["identity_card"] = P._long_text(identity_card, 600)
    if persona is not None:
        sets["persona"] = P._long_text(persona, 400)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        wanted = V.slugify(slug or sets.get("name"), "actor")
        new_slug = V._unique_slug(store, "story_characters", "world_id", int(world["id"]), wanted, None)
        cur = store.db.execute("INSERT INTO story_characters (world_id, slug, name, origin) VALUES (?, ?, ?, ?)",
                               (int(world["id"]), new_slug, sets.get("name") or new_slug,
                                origin if origin in ORIGINS else "agent"))
        character_id = int(cur.lastrowid)
    elif slug:
        sets["slug"] = V._unique_slug(store, "story_characters", "world_id", int(world["id"]),
                                      V.slugify(slug, "actor"), int(character_id))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_characters SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(character_id)))
    if current is not None and "identity_card" in sets and sets["identity_card"] != current["identity_card"]:
        V.mark_stale(store, int(world["project_id"]), T_CHAR, int(character_id))
        _stale_scenes(store, int(world["id"]), char_id=int(character_id))
    if sheet_asset_id:
        _image_asset(store, sheet_asset_id, "лист актёра", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_CHAR, int(character_id), SHEET_KIND, int(sheet_asset_id),
                  prompt="upload")
    return get_character(store, int(character_id))


def save_location(store: Any, world_id: int | None = None, location_id: int | None = None, name: Any = None,
                  description: Any = None, tags: Any = None, slug: Any = None, plate_asset_id: Any = None,
                  origin: str = "agent") -> dict:
    """Локация мира: новая (``world_id``) или правка (``location_id``). ``description`` — ПО-АНГЛИЙСКИ
    (что за комната, мебель, свет), плита — фон без людей 9:16."""
    if location_id:
        current = get_location(store, int(location_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или location_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(name):
            raise StoryError("нужно название локации (name)")
        if not P._text(description):
            raise StoryError("нужно описание локации (description) ПО-АНГЛИЙСКИ: комната, мебель, предметы, свет")
    sets: dict[str, Any] = {}
    if name is not None:
        sets["name"] = P._text(name, 60)
    if description is not None:
        sets["description"] = P._long_text(description, 600)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        wanted = V.slugify(slug or sets.get("name"), "place")
        new_slug = V._unique_slug(store, "story_locations", "world_id", int(world["id"]), wanted, None)
        cur = store.db.execute("INSERT INTO story_locations (world_id, slug, name, origin) VALUES (?, ?, ?, ?)",
                               (int(world["id"]), new_slug, sets.get("name") or new_slug,
                                origin if origin in ORIGINS else "agent"))
        location_id = int(cur.lastrowid)
    elif slug:
        sets["slug"] = V._unique_slug(store, "story_locations", "world_id", int(world["id"]),
                                      V.slugify(slug, "place"), int(location_id))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_locations SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(location_id)))
    if current is not None and "description" in sets and sets["description"] != current["description"]:
        V.mark_stale(store, int(world["project_id"]), T_LOC, int(location_id))
        _stale_scenes(store, int(world["id"]), loc_id=int(location_id))
    if plate_asset_id:
        _image_asset(store, plate_asset_id, "плита локации", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_LOC, int(location_id), PLATE_KIND, int(plate_asset_id),
                  prompt="upload")
    return get_location(store, int(location_id))


def _stale_scenes(store: Any, world_id: int, char_id: int | None = None, loc_id: int | None = None) -> None:
    world = get_world(store, world_id)
    for scene in list_scenes(store, world_id):
        if (char_id is not None and char_id in scene["cast"]) or (loc_id is not None and scene["location_id"] == loc_id):
            V.mark_stale(store, int(world["project_id"]), T_SCENE, int(scene["id"]))


def _scene_in_use(store: Any, world_id: int, scene_id: int) -> list[int]:
    """Несобранные эпизоды мира, у которых сцена в битах."""
    marks = ", ".join("?" for _ in ACTIVE_STATUSES)
    rows = store.db.query_all(f"SELECT id, beats_json FROM story_episodes WHERE world_id = ? AND status IN ({marks})",
                              (int(world_id), *ACTIVE_STATUSES))
    return [int(r["id"]) for r in rows
            if any(int(b.get("scene_id") or 0) == int(scene_id) for b in _loads(r["beats_json"], []))]


def delete_character(store: Any, character_id: int) -> dict:
    c = get_character(store, character_id)
    used = [s["id"] for s in list_scenes(store, int(c["world_id"])) if int(character_id) in s["cast"]]
    if used:
        raise StoryError(f"актёр есть в сценах {used[:12]} — сначала убери их (story_delete_scene)")
    _drop_refs(store, int(c["project_id"]), T_CHAR, int(character_id))
    store.db.execute("DELETE FROM story_characters WHERE id = ?", (int(character_id),))
    return {"ok": True, "deleted": int(character_id)}


def delete_location(store: Any, location_id: int) -> dict:
    loc = get_location(store, location_id)
    used = [s["id"] for s in list_scenes(store, int(loc["world_id"])) if s["location_id"] == int(location_id)]
    if used:
        raise StoryError(f"локация есть в сценах {used[:12]} — сначала убери их (story_delete_scene)")
    _drop_refs(store, int(loc["project_id"]), T_LOC, int(location_id))
    store.db.execute("DELETE FROM story_locations WHERE id = ?", (int(location_id),))
    return {"ok": True, "deleted": int(location_id)}


def sheet_path(store: Any, character: dict) -> Path | None:
    from app.aivideo_gen import _asset_path

    return _asset_path(store, V.selected_ref(store, int(character["project_id"]), T_CHAR, int(character["id"]),
                                             SHEET_KIND))


def plate_path(store: Any, location: dict) -> Path | None:
    from app.aivideo_gen import _asset_path

    return _asset_path(store, V.selected_ref(store, int(location["project_id"]), T_LOC, int(location["id"]),
                                             PLATE_KIND))


# --------------------------------------------------------------------------- #
# Библиотека сцен и переиспользование
# --------------------------------------------------------------------------- #


def _cast(value: Any) -> list[int]:
    out = sorted({int(v) for v in value or [] if str(v).strip().lstrip("-").isdigit() and int(v) > 0})
    return out[:4]


def _scene_view(row: Any, ref: dict | None) -> dict:
    data = dict(row)
    data["cast"] = _loads(data.pop("cast_json", "[]"), [])
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["ready"] = bool(ref and ref.get("asset_id"))
    data["asset_id"] = int(ref["asset_id"]) if data["ready"] else None
    data["url"] = ref["url"] if data["ready"] else None
    data["ref_id"] = int(ref["id"]) if ref else None
    data["version"] = int(ref["version"]) if ref else 0
    data["stale"] = bool(ref and ref.get("stale"))
    return data


def get_scene(store: Any, scene_id: int) -> dict:
    row = store.db.query_one("SELECT s.*, w.project_id FROM story_scenes s JOIN story_worlds w ON w.id = s.world_id "
                             "WHERE s.id = ?", (int(scene_id),))
    if not row:
        raise KeyError(f"нет сцены {scene_id}")
    return _scene_view(row, V.selected_ref(store, int(row["project_id"]), T_SCENE, int(scene_id), SCENE_KIND))


def list_scenes(store: Any, world_id: int, ready_only: bool = False) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT * FROM aiv_refs WHERE project_id = ? AND target_type = ? AND kind = ? "
                              "AND selected = 1", (int(world["project_id"]), T_SCENE, SCENE_KIND))
    refs = {int(r["target_id"]): V.ref_view(r) for r in rows}
    scenes = store.db.query_all("SELECT s.*, w.project_id FROM story_scenes s JOIN story_worlds w ON w.id = s.world_id "
                                "WHERE s.world_id = ? ORDER BY s.id", (int(world_id),))
    out = [_scene_view(r, refs.get(int(r["id"]))) for r in scenes]
    return [s for s in out if s["ready"]] if ready_only else out


def save_scene(store: Any, world_id: int | None = None, scene_id: int | None = None, location_id: Any = None,
               cast: Any = None, action: Any = None, accent: Any = None, title: Any = None, tags: Any = None,
               origin: str = "agent", asset_id: Any = None) -> dict:
    """Сцена библиотеки: новая (``world_id``) или правка (``scene_id``). ``asset_id`` — готовая картинка
    9:16 (загрузка) → версия сцены; без картинки сцена рисуется в эпизоде, где понадобилась."""
    if scene_id:
        current = get_scene(store, int(scene_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или scene_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(action):
            raise StoryError("нужно действие сцены (action) ПО-АНГЛИЙСКИ: кто что делает, поза, выражение, предметы")
    sets: dict[str, Any] = {}
    if location_id is not None:
        if location_id in (0, "0", ""):
            sets["location_id"] = None
        else:
            loc = get_location(store, int(location_id))
            if int(loc["world_id"]) != int(world["id"]):
                raise StoryError(f"локация {location_id} из другого мира")
            sets["location_id"] = int(loc["id"])
    if cast is not None:
        ids = _cast(cast)
        for cid in ids:
            if int(get_character(store, cid)["world_id"]) != int(world["id"]):
                raise StoryError(f"актёр {cid} из другого мира")
        sets["cast_json"] = _json(ids)
    if action is not None:
        sets["action"] = P._long_text(action, 600)
    if accent is not None:
        sets["accent"] = P._text(accent, 120)
    if title is not None:
        sets["title"] = P._text(title, 80)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        cur = store.db.execute("INSERT INTO story_scenes (world_id, origin) VALUES (?, ?)",
                               (int(world["id"]), origin if origin in ORIGINS else "agent"))
        scene_id = int(cur.lastrowid)
        sets.setdefault("slug", f"s{scene_id}")
        sets.setdefault("title", P._text(action, 60))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_scenes SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(scene_id)))
    if asset_id:
        _image_asset(store, asset_id, "картинка сцены", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_SCENE, int(scene_id), SCENE_KIND, int(asset_id), prompt="upload")
    return get_scene(store, int(scene_id))


def delete_scene(store: Any, scene_id: int) -> dict:
    scene = get_scene(store, scene_id)
    used = _scene_in_use(store, int(scene["world_id"]), int(scene_id))
    if used:
        raise StoryError(f"сцена в работе у эпизодов {used} — дождись сборки или перепланируй их")
    _drop_refs(store, int(scene["project_id"]), T_SCENE, int(scene_id))
    store.db.execute("DELETE FROM story_scenes WHERE id = ?", (int(scene_id),))
    return {"ok": True, "deleted": int(scene_id)}


def mark_used(store: Any, scene_ids: list[int]) -> None:
    for sid in set(scene_ids):
        store.db.execute("UPDATE story_scenes SET uses = uses + 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (int(sid),))


def scene_terms(spec: dict) -> set[str]:
    """Основы слов действия, тегов и акцента (русские и английские вместе)."""
    return E.stems(" ".join([str(spec.get("action") or ""), " ".join(spec.get("tags") or []),
                             str(spec.get("accent") or "")]))


def scene_similarity(a: dict, b: dict) -> float:
    return round(E._jaccard(scene_terms(a), scene_terms(b)), 3)


def match_scene(scenes: list[dict], spec: dict, threshold: float = REUSE_MIN) -> tuple[dict | None, float]:
    """Готовая сцена для кадра: то же место, тот же состав актёров, действие похоже (≥ порога)."""
    loc = int(spec.get("location_id") or 0)
    cast = _cast(spec.get("cast"))
    best, best_sim = None, 0.0
    for scene in scenes:
        if int(scene.get("location_id") or 0) != loc or _cast(scene.get("cast")) != cast:
            continue
        sim = scene_similarity(spec, scene)
        if sim > best_sim or (sim == best_sim and best is not None and scene.get("ready") and not best.get("ready")):
            best, best_sim = scene, sim
    return (best, best_sim) if best is not None and best_sim >= threshold else (None, best_sim)


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
    data["editor_url"] = timeline.get("editor_url") if data.get("clip_id") else None
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
                   options: dict | None = None, requested_by: str = "agent") -> dict:
    """Новый эпизод. Жанр — ``options.format`` или формат мира; у сериала-катастрофы эпизод — часть
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
        "INSERT INTO story_episodes (project_id, world_id, premise, duration_target, tone, options_json, requested_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(world["project_id"]), int(world_id), premise, round(seconds, 1),
         P._text(tone, 60) or ("напряжённо" if fmt == FORMAT_DISASTER else TONES[0]),
         _json(options), P._text(requested_by, 40)))
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

    voice = world_voice(store, get_world(store, int(ep["world_id"])))
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
        for sid in beat_scene_ids(ep):
            if sid in seen:
                continue
            seen.add(sid)
            scene = get_scene(store, sid)
            if not scene["ready"]:
                new_scenes.append(scene)
        if not new_scenes:
            return out
        # Основа новых сцен: кадр стиля мира (нет ни одного референса стиля), листы и плиты — до сцен,
        # в той же карточке (работник ждёт их: deps).
        if not world["style_refs"]:
            out.append(self._image(store, project_id, T_WORLD, int(world["id"]), STYLE_KIND,
                                   f"кадр стиля «{world['name']}»", group, slot="1"))
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
                out.append(self._image(store, project_id, T_CHAR, cid, SHEET_KIND, f"лист актёра «{c['name']}»", group))
        for lid in locs:
            loc = get_location(store, lid)
            if not loc["plate"]:
                out.append(self._image(store, project_id, T_LOC, lid, PLATE_KIND, f"плита «{loc['name']}»", group))
        out += [self._scene_item(store, project_id, scene, group) for scene in new_scenes]
        return out

    def _image(self, store: Any, project_id: int, target_type: str, target_id: int, kind: str, label: str,
               group: str, slot: str = "") -> dict:
        from app import aivideo_gen as G

        style = V.get_style(store, project_id)
        item = G._image_item(STAGE, target_type, target_id, kind, label, style["image_model"], style["quality"],
                             style["provider"], slot)
        item["aspect"] = self.aspects.get(kind, "9:16")
        return {**item, "group": group} if group else item

    def _scene_item(self, store: Any, project_id: int, scene: dict, group: str) -> dict:
        title = scene["title"] or scene["action"]
        return self._image(store, project_id, T_SCENE, int(scene["id"]), SCENE_KIND, f"сцена «{P._text(title, 60)}»",
                           group)

    def _narration_item(self, store: Any, ep: dict, regenerate: bool) -> dict | None:
        from app.aivideo_providers import get_tts

        text = narration_text(ep)
        if not text:
            raise StoryError(f"у эпизода {ep['id']} нет текста — сначала сценарий")
        if not regenerate and narration_ref(store, ep) is not None:
            return None
        voice = world_voice(store, get_world(store, int(ep["world_id"])))
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
