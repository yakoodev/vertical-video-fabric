"""ИИ-видео: проект, где ролик рисуется с нуля (docs/design/ai-video-projects.md, фаза MVP).

Проект-серия вида ``ai_video`` (``projects.kind``) получает свои сущности:

* стиль-библия (``aiv_projects``): вид стиля (аниме/2D или фотореализм), стиль-паспорт — фраза,
  которая дословно идёт в каждый промпт, палитра, закреплённая модель картинок, провайдер
  (``none`` — бесплатный сухой прогон, ``polza`` — платно), режим серии (черновик/качество),
  месячный потолок проекта, стиль субтитров;
* персонажи (``aiv_characters``): карта личности (вставляется в промпт ДОСЛОВНО), голос TTS,
  облики по сюжету (``aiv_looks``); свои витуберы владельца — с его картинками-референсами
  (загрузка в проект, никаких фото из интернета);
* локации (``aiv_locations``) — «плита» фона на каждую;
* серии (``aiv_scripts``) и планы (``aiv_shots``): длительность, камера, кто в кадре в каком
  облике, где, реплики по говорящим, ``lipsync`` (на будущее, v2);
* картинки и звук всех этапов — версии (``aiv_refs``): файл в «Файлах для монтажа», выбранная
  версия, «устарело» после правки карты/облика/локации, оценка постоянства.

Здесь только данные (бесплатно). Генерация, деньги и одобрения — ``app/aivideo_gen.py``,
сценарий Gemini — ``app/aivideo_script.py``, сборка холст-клипа — ``app/aivideo_assemble.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app import projects as P
from app.aivideo_providers import provider_ids

AIV_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS aiv_projects (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        style_kind TEXT NOT NULL DEFAULT 'anime',
        passport_text TEXT NOT NULL DEFAULT '',
        palette_json TEXT NOT NULL DEFAULT '[]',
        negative_text TEXT NOT NULL DEFAULT '',
        image_model TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT 'none',
        quality TEXT NOT NULL DEFAULT 'draft',
        month_cap_rub REAL,
        captions_style TEXT NOT NULL DEFAULT 'hormozi',
        music_track_id INTEGER,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aiv_characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT 'invented',
        identity_card TEXT NOT NULL DEFAULT '',
        voice_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aiv_looks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id INTEGER NOT NULL REFERENCES aiv_characters(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        outfit_text TEXT NOT NULL DEFAULT '',
        beat TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (character_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aiv_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aiv_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        episode_no INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL DEFAULT '',
        logline TEXT NOT NULL DEFAULT '',
        brief TEXT NOT NULL DEFAULT '',
        duration_target REAL NOT NULL DEFAULT 30,
        quality TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        rev INTEGER NOT NULL DEFAULT 0,
        clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aiv_shots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_id INTEGER NOT NULL REFERENCES aiv_scripts(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL DEFAULT 1,
        duration REAL NOT NULL DEFAULT 4,
        location TEXT NOT NULL DEFAULT '',
        cast_json TEXT NOT NULL DEFAULT '[]',
        camera_json TEXT NOT NULL DEFAULT '{}',
        dialogue_json TEXT NOT NULL DEFAULT '[]',
        action TEXT NOT NULL DEFAULT '',
        image_prompt TEXT NOT NULL DEFAULT '',
        motion_prompt TEXT NOT NULL DEFAULT '',
        needs_motion INTEGER NOT NULL DEFAULT 0,
        lipsync INTEGER NOT NULL DEFAULT 0,
        transition_in TEXT NOT NULL DEFAULT 'cut',
        locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aiv_shots_script ON aiv_shots(script_id, idx)",
    # Версии картинок и звука: target_type style|character|look|location|shot|line; slot — номер
    # реплики у озвучки плана (kind line_audio), у остального пусто.
    """
    CREATE TABLE IF NOT EXISTS aiv_refs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL DEFAULT 0,
        kind TEXT NOT NULL,
        slot TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        asset_id INTEGER REFERENCES montage_assets(id) ON DELETE SET NULL,
        job_id INTEGER,
        prompt TEXT NOT NULL DEFAULT '',
        selected INTEGER NOT NULL DEFAULT 0,
        stale INTEGER NOT NULL DEFAULT 0,
        score_json TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aiv_refs_target ON aiv_refs(project_id, target_type, target_id, kind)",
    # Запуск генерации. status: awaiting (ждёт одобрения) → approved → submitting → polling →
    # done | failed | held (упёрся в потолок/выключено) | cancelled | blocked_policy.
    # remote_id пишется ДО опроса: после перезапуска опрос продолжается без нового POST.
    """
    CREATE TABLE IF NOT EXISTS aiv_gen_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        script_id INTEGER REFERENCES aiv_scripts(id) ON DELETE SET NULL,
        approval_id INTEGER,
        stage TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'image',
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL DEFAULT 0,
        ref_kind TEXT NOT NULL,
        slot TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT 'none',
        model TEXT NOT NULL DEFAULT '',
        params_json TEXT NOT NULL DEFAULT '{}',
        prompt TEXT NOT NULL DEFAULT '',
        input_hash TEXT NOT NULL DEFAULT '',
        estimate_rub REAL NOT NULL DEFAULT 0,
        cost_rub REAL,
        cost_source TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'awaiting',
        remote_id TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        result_ref_id INTEGER,
        requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aiv_jobs_status ON aiv_gen_jobs(status, id)",
    "CREATE INDEX IF NOT EXISTS idx_aiv_jobs_approval ON aiv_gen_jobs(approval_id)",
    # Журнал трат проекта: каждое списание (факт провайдера, разница баланса или оценка).
    """
    CREATE TABLE IF NOT EXISTS aiv_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        script_id INTEGER,
        job_id INTEGER,
        approval_id INTEGER,
        stage TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        amount_rub REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aiv_ledger_project ON aiv_ledger(project_id, created_at)",
]


# v2 (видео планов и постоянство склеек): колонки поверх таблиц MVP.
AIV_COLUMNS = [
    # Провайдер, модель и разрешение видео планов (этап video); пусто — провайдер проекта и
    # AIVIDEO_VIDEO_MODEL. Провайдер видео может отличаться от провайдера картинок.
    ("aiv_projects", "video_provider", "TEXT NOT NULL DEFAULT ''"),
    ("aiv_projects", "video_model", "TEXT NOT NULL DEFAULT ''"),
    ("aiv_projects", "video_resolution", "TEXT NOT NULL DEFAULT '720p'"),
    # Состояние на НАЧАЛО плана: по персонажам поза/куда смотрит/где в кадре/что в руках/одежда,
    # плюс реквизит («один носок снят») — Gemini держит его от плана к плану.
    ("aiv_shots", "state_json", "TEXT NOT NULL DEFAULT '{}'"),
    # План продолжает прошлый (та же сцена): старт видео — последний кадр видео прошлого плана.
    ("aiv_shots", "continues_from_prev", "INTEGER NOT NULL DEFAULT 0"),
    ("aiv_shots", "sound", "TEXT NOT NULL DEFAULT ''"),
    # Окно видео плана в сборке (секунды файла): агент обрезает начало/хвост.
    ("aiv_shots", "video_in", "REAL"),
    ("aiv_shots", "video_out", "REAL"),
    # Проверка склейки Gemini-зрением: {ok, score, mismatches, checked_at}.
    ("aiv_shots", "continuity_json", "TEXT NOT NULL DEFAULT ''"),
    # Время отправки POST (UTC ISO), пишется ДО запроса: по нему id ищется в истории Polza после
    # 504/обрыва и после перезапуска.
    ("aiv_gen_jobs", "submitted_at", "TEXT NOT NULL DEFAULT ''"),
]


def ensure_aiv_schema(conn) -> None:
    """Таблицы ИИ-видео (из ``Database.init`` после проектов и автопилота)."""
    for sql in AIV_SCHEMA_SQL:
        conn.execute(sql)
    for table, column, definition in AIV_COLUMNS:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    # Карточка этапа (kind other, action aiv_budget) — свой обработчик решения владельца.
    import app.aivideo_gen  # noqa: F401


# --------------------------------------------------------------------------- #
# Константы
# --------------------------------------------------------------------------- #

PROJECT_KIND = "ai_video"
STYLE_KINDS = ("anime", "photoreal")
STYLE_LABELS = {"anime": "аниме / 2D-toon", "photoreal": "фотореализм"}
QUALITIES = ("draft", "quality")
QUALITY_LABELS = {"draft": "черновик", "quality": "качество"}
PROVIDERS = provider_ids()  # подключаемые: app/aivideo_providers.py CATALOGS
ORIGINS = ("invented", "owner")
SCRIPT_STATUSES = ("draft", "approved", "producing", "assembled")
CAMERA_MOVES = ("static", "push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
                "parallax_push", "orbit", "float", "dolly_zoom", "tilt_reveal")
CAMERA_MOVE_ALIASES = {"push": "push_in", "zoom_in": "push_in", "zoom_out": "pull_out", "pull": "pull_out",
                       "pan": "pan_right", "still": "static", "none": "static", "hold": "static",
                       "parallax": "parallax_push", "arc": "orbit", "breathe": "float", "drift": "float",
                       "vertigo": "dolly_zoom", "reveal": "tilt_reveal", "tilt": "tilt_up"}
SHOT_SIZES = ("extreme_wide", "wide", "full", "medium", "medium_close", "close", "extreme_close")
TRANSITIONS = ("cut", "fade")
SHOT_MIN_SEC = 2.0
SHOT_MAX_SEC = 8.0
MAX_SHOTS = 20
MAX_CAST = 2  # больше двух персонажей в кадре — «другой человек» (риск постоянства)
MAX_LINES_PER_SHOT = 4
CHARS_PER_SEC = 14.0  # как DRY_CHARS_PER_SEC в dubbing: столько символов русской речи в секунду
SLUG_RE = re.compile(r"[^a-z0-9_]+")
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


class AivError(ValueError):
    """Понятная ошибка ИИ-видео (нет проекта, неверный план, этап не разрешён)."""


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def slugify(value: Any, fallback: str = "x") -> str:
    text = "".join(TRANSLIT.get(ch, ch) for ch in str(value or "").strip().lower())
    text = SLUG_RE.sub("_", text).strip("_")
    return text[:32] or fallback


def _hexes(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\s;]+", value)
    out = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
            out.append("#" + text.lstrip("#").lower())
    return out[:8]


def _now_sql() -> str:
    return "CURRENT_TIMESTAMP"


# --------------------------------------------------------------------------- #
# Проект и стиль-библия
# --------------------------------------------------------------------------- #

STYLE_FIELDS = ("style_kind", "passport_text", "palette", "negative_text", "image_model", "provider", "quality",
                "captions_style", "music_track_id", "video_provider", "video_model", "video_resolution")
VIDEO_RESOLUTIONS = ("480p", "720p")
# Потолок месяца — только владелец (REST с cookie), не агент.
OWNER_FIELDS = ("month_cap_rub",)


def require_project(store: Any, project_id: int) -> dict:
    project = P.get_project(store, int(project_id), include_sources=False)
    if project.get("kind") != PROJECT_KIND:
        raise AivError(f"проект #{project_id} — не «ИИ-видео» (kind {project.get('kind')}); "
                       "создай aiv_create_project или смени тип проекта на ai_video")
    return project


def _style_row(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_projects WHERE project_id = ?", (int(project_id),))
    if not row:
        store.db.execute("INSERT OR IGNORE INTO aiv_projects (project_id) VALUES (?)", (int(project_id),))
        row = store.db.query_one("SELECT * FROM aiv_projects WHERE project_id = ?", (int(project_id),))
    data = dict(row)
    data["palette"] = _loads(data.pop("palette_json", "[]"), [])
    return data


def get_style(store: Any, project_id: int) -> dict:
    from app.settings import settings

    require_project(store, project_id)
    data = _style_row(store, project_id)
    data["image_model"] = data.get("image_model") or settings.aivideo_image_model
    data["video_provider"] = data.get("video_provider") or data["provider"]
    data["video_model"] = data.get("video_model") or settings.aivideo_video_model
    data["video_resolution"] = data.get("video_resolution") or "720p"
    data["style_label"] = STYLE_LABELS.get(data["style_kind"], data["style_kind"])
    data["quality_label"] = QUALITY_LABELS.get(data["quality"], data["quality"])
    return data


def update_style(store: Any, project_id: int, patch: dict, owner: bool = False) -> dict:
    """Стиль-библия и настройки проекта. ``owner`` — можно менять потолок месяца."""
    from app.aivideo_providers import catalog

    require_project(store, project_id)
    allowed = STYLE_FIELDS + (OWNER_FIELDS if owner else ())
    sets: dict[str, Any] = {}
    current = _style_row(store, project_id)
    image_provider = (patch or {}).get("provider") or current["provider"]
    video_provider = (patch or {}).get("video_provider") or current.get("video_provider") or image_provider
    for key, value in (patch or {}).items():
        if key not in allowed:
            if key in OWNER_FIELDS:
                raise AivError("потолок месяца меняет только владелец (в интерфейсе проекта)")
            continue
        if key == "style_kind":
            sets[key] = P._choice(value, STYLE_KINDS, "style_kind")
        elif key == "provider":
            sets[key] = P._choice(value, PROVIDERS, "provider")
        elif key == "quality":
            sets[key] = P._choice(value, QUALITIES, "quality")
        elif key == "palette":
            sets["palette_json"] = _json(_hexes(value))
        elif key == "video_provider":
            sets[key] = P._choice(value, PROVIDERS, "video_provider") if value else ""
        elif key == "image_model":
            model = str(value or "").strip()
            models = _catalog_models(catalog, image_provider, "image")
            if model and model not in models:
                raise AivError(f"image_model у {image_provider}: одна из {', '.join(models)}")
            sets[key] = model
        elif key == "video_model":
            model = str(value or "").strip()
            models = _catalog_models(catalog, video_provider, "video")
            if model and model not in models:
                raise AivError(f"video_model у {video_provider}: одна из {', '.join(models)} (kling/v3 режет план "
                               "на склейки — для сцепки планов не годится)")
            sets[key] = model
        elif key == "video_resolution":
            sets[key] = P._choice(value or "720p", VIDEO_RESOLUTIONS, "video_resolution")
        elif key == "passport_text":
            sets[key] = P._long_text(value, 600)
        elif key == "negative_text":
            sets[key] = P._long_text(value, 400)
        elif key == "captions_style":
            sets[key] = P._text(value, 40) or "hormozi"
        elif key == "music_track_id":
            sets[key] = int(value) if value not in (None, "", 0) else None
        elif key == "month_cap_rub":
            sets[key] = max(0.0, float(value)) if value not in (None, "") else None
    if sets:
        _style_row(store, project_id)
        if "passport_text" in sets or "palette_json" in sets or "style_kind" in sets:
            mark_stale(store, project_id, "style", 0)
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE aiv_projects SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
                         (*sets.values(), int(project_id)))
    return get_style(store, project_id)


def _catalog_models(catalog: Any, provider: str, kind: str) -> list[str]:
    try:
        cat = catalog(provider)
    except RuntimeError as exc:  # ProviderError: нет такого провайдера
        raise AivError(str(exc)) from exc
    return cat.image_models() if kind == "image" else cat.video_models()


def create_ai_project(store: Any, name: str, style_kind: str = "anime", passport_text: str = "", palette: Any = None,
                      quality: str = "draft", provider: str = "none", description: str = "",
                      image_model: str = "", video_model: str = "") -> dict:
    project = P.create_project(store, name, kind=PROJECT_KIND, description=description)
    update_style(store, project["id"], {"style_kind": style_kind or "anime", "passport_text": passport_text,
                                        "palette": palette or [], "quality": quality or "draft",
                                        "provider": provider or "none", "image_model": image_model,
                                        "video_model": video_model})
    return overview(store, project["id"])


# --------------------------------------------------------------------------- #
# Персонажи, облики, локации
# --------------------------------------------------------------------------- #


def _voice(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ("provider", "model", "voice"):
        text = P._text(value.get(key), 80)
        if text:
            out[key] = text
    if out.get("provider") and out["provider"] not in PROVIDERS:
        raise AivError("voice.provider: polza или none")
    return out


def _unique_slug(store: Any, table: str, scope_col: str, scope_id: int, wanted: str, own_id: int | None) -> str:
    slug, n = wanted, 2
    while True:
        row = store.db.query_one(f"SELECT id FROM {table} WHERE {scope_col} = ? AND slug = ?", (scope_id, slug))
        if not row or (own_id is not None and int(row["id"]) == int(own_id)):
            return slug
        slug, n = f"{wanted[:29]}_{n}", n + 1


def get_character(store: Any, character_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_characters WHERE id = ?", (int(character_id),))
    if not row:
        raise KeyError(f"нет персонажа {character_id}")
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
    data["looks"] = [dict(r) for r in store.db.query_all(
        "SELECT * FROM aiv_looks WHERE character_id = ? ORDER BY id", (int(character_id),))]
    return data


def character_by_slug(store: Any, project_id: int, slug: str) -> dict | None:
    row = store.db.query_one("SELECT id FROM aiv_characters WHERE project_id = ? AND slug = ?",
                             (int(project_id), str(slug or "")))
    return get_character(store, int(row["id"])) if row else None


def list_characters(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id FROM aiv_characters WHERE project_id = ? ORDER BY id", (int(project_id),))
    return [get_character(store, int(r["id"])) for r in rows]


def save_character(store: Any, project_id: int, name: str = "", identity_card: Any = None, slug: str = "",
                   origin: str | None = None, voice: Any = None, notes: Any = None,
                   character_id: int | None = None) -> dict:
    """Новый персонаж или правка. Смена карты личности помечает его картинки и кадры с ним «устарело»."""
    require_project(store, project_id)
    if character_id:
        current = get_character(store, character_id)
        if int(current["project_id"]) != int(project_id):
            raise AivError("персонаж из другого проекта")
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if identity_card is not None and P._long_text(identity_card, 1500) != current["identity_card"]:
            sets["identity_card"] = P._long_text(identity_card, 1500)
        if origin:
            sets["origin"] = P._choice(origin, ORIGINS, "origin")
        if voice is not None:
            sets["voice_json"] = _json(_voice(voice))
        if notes is not None:
            sets["notes"] = P._long_text(notes, 1000)
        if slug and slugify(slug) != current["slug"]:
            raise AivError("slug персонажа не меняется (на него ссылаются планы) — заведи нового")
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_characters SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                             (*sets.values(), int(character_id)))
            if "identity_card" in sets:
                mark_stale(store, project_id, "character", int(character_id))
        return get_character(store, character_id)
    name = P._text(name, 80)
    if not name:
        raise AivError("нужно имя персонажа")
    card = P._long_text(identity_card, 1500)
    if not card:
        raise AivError("нужна карта личности: возраст, волосы, глаза, рост и пропорции, 2–3 цвета в hex, "
                       "отличительная деталь — она идёт в каждый промпт дословно")
    slug = _unique_slug(store, "aiv_characters", "project_id", int(project_id), slugify(slug or name, "hero"), None)
    cur = store.db.execute(
        "INSERT INTO aiv_characters (project_id, slug, name, origin, identity_card, voice_json, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), slug, name, P._choice(origin or "invented", ORIGINS, "origin"), card,
         _json(_voice(voice)), P._long_text(notes, 1000)))
    return get_character(store, int(cur.lastrowid))


def delete_character(store: Any, character_id: int) -> dict:
    current = get_character(store, character_id)
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'character' AND target_id = ?", (int(character_id),))
    for look in current["looks"]:
        store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'look' AND target_id = ?", (int(look["id"]),))
    store.db.execute("DELETE FROM aiv_characters WHERE id = ?", (int(character_id),))
    return {"ok": True, "deleted": int(character_id)}


def save_look(store: Any, character_id: int, name: str = "", outfit_text: Any = None, beat: Any = None,
              slug: str = "", look_id: int | None = None) -> dict:
    """Облик персонажа на отрезке сюжета («в пижаме», «промокла»): правка канона — меняется только одежда/состояние."""
    character = get_character(store, character_id)
    if look_id:
        row = store.db.query_one("SELECT * FROM aiv_looks WHERE id = ? AND character_id = ?",
                                 (int(look_id), int(character_id)))
        if not row:
            raise KeyError(f"нет облика {look_id}")
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if outfit_text is not None and P._long_text(outfit_text, 800) != row["outfit_text"]:
            sets["outfit_text"] = P._long_text(outfit_text, 800)
        if beat is not None:
            sets["beat"] = P._text(beat, 120)
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_looks SET {cols} WHERE id = ?", (*sets.values(), int(look_id)))
            if "outfit_text" in sets:
                mark_stale(store, character["project_id"], "look", int(look_id))
        return get_character(store, character_id)
    name = P._text(name, 80)
    outfit = P._long_text(outfit_text, 800)
    if not name or not outfit:
        raise AivError("облику нужны name и outfit_text (что меняется: одежда, состояние)")
    slug = _unique_slug(store, "aiv_looks", "character_id", int(character_id), slugify(slug or name, "look"), None)
    store.db.execute("INSERT INTO aiv_looks (character_id, slug, name, outfit_text, beat) VALUES (?, ?, ?, ?, ?)",
                     (int(character_id), slug, name, outfit, P._text(beat, 120)))
    return get_character(store, character_id)


def delete_look(store: Any, look_id: int) -> dict:
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'look' AND target_id = ?", (int(look_id),))
    store.db.execute("DELETE FROM aiv_looks WHERE id = ?", (int(look_id),))
    return {"ok": True, "deleted": int(look_id)}


def get_look(store: Any, look_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_looks WHERE id = ?", (int(look_id),))
    if not row:
        raise KeyError(f"нет облика {look_id}")
    return dict(row)


def get_location(store: Any, location_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_locations WHERE id = ?", (int(location_id),))
    if not row:
        raise KeyError(f"нет локации {location_id}")
    return dict(row)


def location_by_slug(store: Any, project_id: int, slug: str) -> dict | None:
    row = store.db.query_one("SELECT * FROM aiv_locations WHERE project_id = ? AND slug = ?",
                             (int(project_id), str(slug or "")))
    return dict(row) if row else None


def list_locations(store: Any, project_id: int) -> list[dict]:
    return [dict(r) for r in store.db.query_all("SELECT * FROM aiv_locations WHERE project_id = ? ORDER BY id",
                                                (int(project_id),))]


def save_location(store: Any, project_id: int, name: str = "", description: Any = None, slug: str = "",
                  location_id: int | None = None) -> dict:
    require_project(store, project_id)
    if location_id:
        current = get_location(store, location_id)
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if description is not None and P._long_text(description, 800) != current["description"]:
            sets["description"] = P._long_text(description, 800)
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_locations SET {cols} WHERE id = ?", (*sets.values(), int(location_id)))
            if "description" in sets:
                mark_stale(store, project_id, "location", int(location_id))
        return get_location(store, location_id)
    name = P._text(name, 80)
    if not name:
        raise AivError("нужно имя локации")
    slug = _unique_slug(store, "aiv_locations", "project_id", int(project_id), slugify(slug or name, "place"), None)
    cur = store.db.execute("INSERT INTO aiv_locations (project_id, slug, name, description) VALUES (?, ?, ?, ?)",
                           (int(project_id), slug, name, P._long_text(description, 800)))
    return get_location(store, int(cur.lastrowid))


def delete_location(store: Any, location_id: int) -> dict:
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'location' AND target_id = ?", (int(location_id),))
    store.db.execute("DELETE FROM aiv_locations WHERE id = ?", (int(location_id),))
    return {"ok": True, "deleted": int(location_id)}


# --------------------------------------------------------------------------- #
# Серии и планы
# --------------------------------------------------------------------------- #


def get_script(store: Any, script_id: int, with_shots: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_scripts WHERE id = ?", (int(script_id),))
    if not row:
        raise KeyError(f"нет серии {script_id}")
    data = dict(row)
    if not data.get("quality"):
        data["quality"] = _style_row(store, data["project_id"])["quality"]
    if with_shots:
        data["shots"] = list_shots(store, script_id)
        data["duration_total"] = round(sum(float(s["duration"]) for s in data["shots"]), 2)
    return data


def list_scripts(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id FROM aiv_scripts WHERE project_id = ? ORDER BY episode_no, id",
                              (int(project_id),))
    out = []
    for r in rows:
        script = get_script(store, int(r["id"]), with_shots=True)
        script["shots_count"] = len(script.pop("shots"))
        out.append(script)
    return out


def create_script(store: Any, project_id: int, brief: str = "", title: str = "", episode_no: int | None = None,
                  duration_target: Any = None, quality: str | None = None) -> dict:
    require_project(store, project_id)
    if episode_no in (None, "", 0):
        row = store.db.query_one("SELECT MAX(episode_no) AS n FROM aiv_scripts WHERE project_id = ?",
                                 (int(project_id),))
        episode_no = int(row["n"] or 0) + 1 if row else 1
    target = _duration_target(duration_target)
    cur = store.db.execute(
        "INSERT INTO aiv_scripts (project_id, episode_no, title, brief, duration_target, quality) VALUES (?, ?, ?, ?, ?, ?)",
        (int(project_id), int(episode_no), P._text(title, 120) or f"Серия {episode_no}", P._long_text(brief, 3000),
         target, P._choice(quality, QUALITIES, "quality") if quality else ""))
    return get_script(store, int(cur.lastrowid))


def _duration_target(value: Any) -> float:
    try:
        target = float(value) if value not in (None, "") else 30.0
    except (TypeError, ValueError):
        target = 30.0
    return round(min(120.0, max(10.0, target)), 1)


def list_shots(store: Any, script_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM aiv_shots WHERE script_id = ? ORDER BY idx, id", (int(script_id),))
    return [_shot_from_row(r) for r in rows]


def _shot_from_row(row: Any) -> dict:
    data = dict(row)
    data["cast"] = _loads(data.pop("cast_json", "[]"), [])
    data["camera"] = _loads(data.pop("camera_json", "{}"), {})
    data["dialogue"] = _loads(data.pop("dialogue_json", "[]"), [])
    data["needs_motion"] = bool(data.get("needs_motion"))
    data["lipsync"] = bool(data.get("lipsync"))
    data["locked"] = bool(data.get("locked"))
    data["continuity"] = _loads(data.pop("continuity_json", ""), {}) or None
    return data


def get_shot(store: Any, shot_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_shots WHERE id = ?", (int(shot_id),))
    if not row:
        raise KeyError(f"нет плана {shot_id}")
    return _shot_from_row(row)


def camera_move(value: Any) -> str:
    name = str(value or "static").strip().lower().replace("-", "_").replace(" ", "_")
    name = CAMERA_MOVE_ALIASES.get(name, name)
    if name not in CAMERA_MOVES:
        raise AivError(f"camera.move {value!r}: одно из {', '.join(CAMERA_MOVES)}")
    return name


def normalize_shot(store: Any, project_id: int, raw: dict, idx: int, warnings: list[str]) -> dict:
    """План из сценария/правки → поля строки. Неизвестный персонаж/локация — ошибка с подсказкой."""
    if not isinstance(raw, dict):
        raise AivError(f"план {idx}: нужен объект")
    try:
        duration = float(raw.get("duration") or 4)
    except (TypeError, ValueError) as exc:
        raise AivError(f"план {idx}: duration — число секунд") from exc
    if not SHOT_MIN_SEC <= duration <= SHOT_MAX_SEC:
        warnings.append(f"план {idx}: длительность {duration:g} с прижата к {SHOT_MIN_SEC:g}–{SHOT_MAX_SEC:g} с")
        duration = min(SHOT_MAX_SEC, max(SHOT_MIN_SEC, duration))
    location = slugify(raw.get("location") or "", "") if raw.get("location") else ""
    if location and not location_by_slug(store, project_id, location):
        known = ", ".join(l["slug"] for l in list_locations(store, project_id)) or "нет ни одной"
        raise AivError(f"план {idx}: нет локации {location!r} (есть: {known}) — aiv_define_location")
    cast = []
    for item in (raw.get("characters") or raw.get("cast") or [])[:MAX_CAST + 3]:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        slug = slugify(item.get("id") or item.get("character") or item.get("slug") or "", "")
        character = character_by_slug(store, project_id, slug) if slug else None
        if character is None:
            known = ", ".join(c["slug"] for c in list_characters(store, project_id)) or "нет ни одного"
            raise AivError(f"план {idx}: нет персонажа {slug!r} (есть: {known}) — aiv_define_character")
        look = slugify(item.get("look") or "", "") if item.get("look") else ""
        if look and look not in {l["slug"] for l in character["looks"]}:
            warnings.append(f"план {idx}: у {slug} нет облика {look!r} — будет канон (aiv_add_look)")
            look = ""
        cast.append({"id": slug, "look": look, "action": P._text(item.get("action"), 200)})
    if len(cast) > MAX_CAST:
        warnings.append(f"план {idx}: в кадре больше {MAX_CAST} персонажей — оставлены первые {MAX_CAST} "
                        "(толпа — главный источник «другого человека»)")
        cast = cast[:MAX_CAST]
    cam = raw.get("camera") if isinstance(raw.get("camera"), dict) else {"move": raw.get("camera")}
    camera = {"move": camera_move(cam.get("move"))}
    size = str(cam.get("shot") or cam.get("size") or "").strip().lower()
    if size:
        camera["shot"] = size if size in SHOT_SIZES else "medium"
    if cam.get("angle"):
        camera["angle"] = P._text(cam.get("angle"), 30)
    try:
        if cam.get("lens"):
            camera["lens"] = int(min(135, max(14, float(cam["lens"]))))
    except (TypeError, ValueError):
        pass
    speakers = {c["id"] for c in cast}
    dialogue = []
    for line in (raw.get("dialogue") or [])[:MAX_LINES_PER_SHOT]:
        if not isinstance(line, dict):
            continue
        text = P._text(line.get("text"), 300)
        if not text:
            continue
        who = slugify(line.get("who") or line.get("speaker") or "", "")
        if who and who != "narrator" and not character_by_slug(store, project_id, who):
            raise AivError(f"план {idx}: реплику говорит неизвестный {who!r}")
        if who and who != "narrator" and who not in speakers:
            warnings.append(f"план {idx}: {who} говорит за кадром (его нет в characters плана)")
        dialogue.append({"who": who or "narrator", "text": text, "emotion": P._text(line.get("emotion"), 40)})
    chars = sum(len(d["text"]) for d in dialogue)
    if chars and chars / CHARS_PER_SEC > duration + 0.5:
        warnings.append(f"план {idx}: реплик на {chars / CHARS_PER_SEC:.1f} с при плане {duration:g} с — "
                        "при сборке план удлинится под озвучку (или сократи текст)")
    transition = str(raw.get("transition_in") or "cut").strip().lower()
    video_in, video_out = _window(raw.get("video_in")), _window(raw.get("video_out"))
    if video_in is not None and video_out is not None and video_out - video_in < 0.5:
        warnings.append(f"план {idx}: окно видео {video_in:g}–{video_out:g} с короче 0.5 с — сброшено")
        video_in = video_out = None
    return {
        "idx": idx, "duration": round(duration, 2), "location": location,
        "cast_json": _json(cast), "camera_json": _json(camera), "dialogue_json": _json(dialogue),
        "action": P._text(raw.get("action"), 400), "image_prompt": P._long_text(raw.get("image_prompt"), 800),
        "motion_prompt": P._text(raw.get("motion_prompt"), 400), "needs_motion": int(bool(raw.get("needs_motion"))),
        "lipsync": int(bool(raw.get("lipsync"))),
        "transition_in": transition if transition in TRANSITIONS else "cut",
        "sound": P._text(raw.get("sound"), 300), "video_in": video_in, "video_out": video_out,
    }


def _window(value: Any) -> float | None:
    """Секунда файла видео плана (окно сборки); пусто/мусор — None (всё видео)."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(min(30.0, max(0.0, number)), 3)


def replace_shots(store: Any, script_id: int, shots: list, title: str | None = None, logline: str | None = None) -> dict:
    """Весь шот-лист заново (сценарий Gemini или правка агентом). Картинки старых планов остаются в
    библиотеке, но к новым планам не привязаны."""
    script = get_script(store, script_id, with_shots=False)
    if not isinstance(shots, list) or not shots:
        raise AivError("нужен непустой список shots")
    if len(shots) > MAX_SHOTS:
        raise AivError(f"планов {len(shots)} — не больше {MAX_SHOTS}")
    warnings: list[str] = []
    rows = [normalize_shot(store, script["project_id"], raw, i + 1, warnings) for i, raw in enumerate(shots)]
    old = [int(r["id"]) for r in store.db.query_all("SELECT id FROM aiv_shots WHERE script_id = ?", (int(script_id),))]
    for shot_id in old:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type IN ('shot', 'line') AND target_id = ?",
                         (shot_id,))
    store.db.execute("DELETE FROM aiv_shots WHERE script_id = ?", (int(script_id),))
    for row in rows:
        cols = ", ".join(row)
        store.db.execute(f"INSERT INTO aiv_shots (script_id, {cols}) VALUES (?, {', '.join('?' for _ in row)})",
                         (int(script_id), *row.values()))
    sets = {"rev": int(script["rev"]) + 1}
    if title is not None and P._text(title, 120):
        sets["title"] = P._text(title, 120)
    if logline is not None:
        sets["logline"] = P._text(logline, 400)
    cols = ", ".join(f"{k} = ?" for k in sets)
    store.db.execute(f"UPDATE aiv_scripts SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*sets.values(), int(script_id)))
    out = get_script(store, script_id)
    total = out["duration_total"]
    if abs(total - float(script["duration_target"])) > max(5.0, 0.25 * float(script["duration_target"])):
        warnings.append(f"сумма планов {total:g} с против цели {script['duration_target']:g} с")
    out["warnings"] = warnings
    return out


def update_script(store: Any, script_id: int, patch: dict) -> dict:
    script = get_script(store, script_id, with_shots=False)
    if isinstance(patch.get("shots"), list):
        return replace_shots(store, script_id, patch["shots"], patch.get("title"), patch.get("logline"))
    sets: dict[str, Any] = {}
    if patch.get("title"):
        sets["title"] = P._text(patch["title"], 120)
    if "logline" in patch:
        sets["logline"] = P._text(patch["logline"], 400)
    if "brief" in patch:
        sets["brief"] = P._long_text(patch["brief"], 3000)
    if "duration_target" in patch:
        sets["duration_target"] = _duration_target(patch["duration_target"])
    if patch.get("quality"):
        sets["quality"] = P._choice(patch["quality"], QUALITIES, "quality")
    if patch.get("status"):
        sets["status"] = P._choice(patch["status"], SCRIPT_STATUSES, "status")
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE aiv_scripts SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(script["id"])))
    return get_script(store, script_id)


def update_shot(store: Any, shot_id: int, patch: dict) -> dict:
    """Правка одного плана (поля как в шот-листе). Смена кадра/персонажей/локации — кадр «устарел»."""
    shot = get_shot(store, shot_id)
    script = get_script(store, shot["script_id"], with_shots=False)
    merged = {
        "duration": shot["duration"], "location": shot["location"], "characters": shot["cast"],
        "camera": shot["camera"], "dialogue": shot["dialogue"], "action": shot["action"],
        "image_prompt": shot["image_prompt"], "motion_prompt": shot["motion_prompt"],
        "needs_motion": shot["needs_motion"], "lipsync": shot["lipsync"], "transition_in": shot["transition_in"],
        "sound": shot.get("sound") or "", "video_in": shot.get("video_in"), "video_out": shot.get("video_out"),
    }
    for key, value in (patch or {}).items():
        if key == "cast":
            key = "characters"
        if key in merged:
            merged[key] = value
    warnings: list[str] = []
    row = normalize_shot(store, script["project_id"], merged, int(shot["idx"]), warnings)
    row.pop("idx")
    visual = any(row[k] != old for k, old in (
        ("location", shot["location"]), ("cast_json", _json(shot["cast"])), ("action", shot["action"]),
        ("image_prompt", shot["image_prompt"]), ("camera_json", _json(shot["camera"]))))
    if "locked" in (patch or {}):
        row["locked"] = int(bool(patch["locked"]))
    cols = ", ".join(f"{k} = ?" for k in row)
    store.db.execute(f"UPDATE aiv_shots SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*row.values(), int(shot_id)))
    motion = any(row[k] != (shot.get(k) or "") for k in ("motion_prompt", "sound"))
    if visual:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ?", (int(shot_id),))
    elif motion:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                         "AND kind = 'shot_video'", (int(shot_id),))
    out = get_shot(store, shot_id)
    out["warnings"] = warnings
    return out


def delete_script(store: Any, script_id: int) -> dict:
    get_script(store, script_id, with_shots=False)
    for r in store.db.query_all("SELECT id FROM aiv_shots WHERE script_id = ?", (int(script_id),)):
        store.db.execute("DELETE FROM aiv_refs WHERE target_type IN ('shot', 'line') AND target_id = ?", (int(r["id"]),))
    store.db.execute("DELETE FROM aiv_scripts WHERE id = ?", (int(script_id),))
    return {"ok": True, "deleted": int(script_id)}


# --------------------------------------------------------------------------- #
# Версии картинок (refs)
# --------------------------------------------------------------------------- #


def ref_view(row: Any) -> dict:
    data = dict(row)
    data["selected"] = bool(data.get("selected"))
    data["stale"] = bool(data.get("stale"))
    data["score"] = _loads(data.pop("score_json", ""), {}) or None
    data["url"] = f"/media/montage-assets/{data['asset_id']}" if data.get("asset_id") else None
    return data


def get_ref(store: Any, ref_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_refs WHERE id = ?", (int(ref_id),))
    if not row:
        raise KeyError(f"нет версии {ref_id}")
    return ref_view(row)


def list_refs(store: Any, project_id: int, target_type: str | None = None, target_id: int | None = None,
              kind: str | None = None, slot: str | None = None) -> list[dict]:
    where, params = ["project_id = ?"], [int(project_id)]
    for col, value in (("target_type", target_type), ("target_id", target_id), ("kind", kind), ("slot", slot)):
        if value is not None:
            where.append(f"{col} = ?")
            params.append(value)
    rows = store.db.query_all(f"SELECT * FROM aiv_refs WHERE {' AND '.join(where)} ORDER BY id", params)
    return [ref_view(r) for r in rows]


def selected_ref(store: Any, project_id: int, target_type: str, target_id: int, kind: str,
                 slot: str = "") -> dict | None:
    row = store.db.query_one(
        "SELECT * FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ? AND kind = ? AND slot = ? "
        "ORDER BY selected DESC, id DESC LIMIT 1", (int(project_id), target_type, int(target_id), kind, slot))
    return ref_view(row) if row else None


def add_ref(store: Any, project_id: int, target_type: str, target_id: int, kind: str, asset_id: int,
            prompt: str = "", job_id: int | None = None, slot: str = "", select: bool = True) -> dict:
    row = store.db.query_one(
        "SELECT MAX(version) AS v FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ? "
        "AND kind = ? AND slot = ?", (int(project_id), target_type, int(target_id), kind, slot))
    version = int(row["v"] or 0) + 1 if row else 1
    cur = store.db.execute(
        "INSERT INTO aiv_refs (project_id, target_type, target_id, kind, slot, version, asset_id, job_id, prompt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), target_type, int(target_id), kind, slot, version, int(asset_id), job_id, prompt[:6000]))
    ref_id = int(cur.lastrowid)
    if select:
        select_ref(store, ref_id)
    return get_ref(store, ref_id)


def select_ref(store: Any, ref_id: int) -> dict:
    ref = get_ref(store, ref_id)
    store.db.execute(
        "UPDATE aiv_refs SET selected = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE project_id = ? AND target_type = ? "
        "AND target_id = ? AND kind = ? AND slot = ?",
        (int(ref_id), ref["project_id"], ref["target_type"], ref["target_id"], ref["kind"], ref["slot"]))
    return get_ref(store, ref_id)


def set_ref_score(store: Any, ref_id: int, score: dict) -> dict:
    store.db.execute("UPDATE aiv_refs SET score_json = ? WHERE id = ?", (_json(score), int(ref_id)))
    return get_ref(store, ref_id)


def mark_stale(store: Any, project_id: int, target_type: str, target_id: int) -> int:
    """Правка карты/облика/локации/стиля → свои картинки и кадры планов, где это есть, — «устарело».
    Ничего не перегенерируется само: владелец видит «устарело, перегенерировать за ≈X ₽?»."""
    n = 0
    if target_type == "style":
        cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE project_id = ? AND kind != 'upload' "
                               "AND target_type != 'line'", (int(project_id),))
        return int(cur.rowcount or 0)
    cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE project_id = ? AND target_type = ? AND target_id = ? "
                           "AND kind != 'upload'", (int(project_id), target_type, int(target_id)))
    n += int(cur.rowcount or 0)
    slugs: set[str] = set()
    if target_type == "character":
        slugs = {get_character(store, target_id)["slug"]}
        for look in get_character(store, target_id)["looks"]:
            cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'look' AND target_id = ?",
                                   (int(look["id"]),))
            n += int(cur.rowcount or 0)
    elif target_type == "look":
        look = get_look(store, target_id)
        slugs = {f"look:{get_character(store, look['character_id'])['slug']}:{look['slug']}"}
    elif target_type == "location":
        slugs = {f"loc:{get_location(store, target_id)['slug']}"}
    for shot in _project_shots(store, project_id):
        keys = {c["id"] for c in shot["cast"]} | {f"look:{c['id']}:{c['look']}" for c in shot["cast"] if c["look"]}
        if shot["location"]:
            keys.add(f"loc:{shot['location']}")
        if keys & slugs:
            cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ?",
                                   (int(shot["id"]),))
            n += int(cur.rowcount or 0)
    return n


def _project_shots(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT sh.* FROM aiv_shots sh JOIN aiv_scripts sc ON sc.id = sh.script_id "
                              "WHERE sc.project_id = ? ORDER BY sh.id", (int(project_id),))
    return [_shot_from_row(r) for r in rows]


def add_upload_ref(store: Any, project_id: int, target_type: str, target_id: int, asset_id: int) -> dict:
    """Картинка владельца (его витубер/персонаж) — канонический референс. Только загрузка владельцем
    или агентом из его файлов; фото реальных людей из интернета не берём."""
    asset = store.get_montage_asset(int(asset_id))
    if asset.get("kind") not in ("image", "gif"):
        raise AivError("референс — картинка (png/jpg/webp)")
    if target_type == "character":
        if int(get_character(store, target_id)["project_id"]) != int(project_id):
            raise AivError("персонаж из другого проекта")
    elif target_type == "location":
        if int(get_location(store, target_id)["project_id"]) != int(project_id):
            raise AivError("локация из другого проекта")
    elif target_type != "style":
        raise AivError("референс владельца — для персонажа, локации или стиля")
    return add_ref(store, project_id, target_type, int(target_id), "upload", int(asset_id),
                   prompt=f"загрузка владельца: {asset.get('label') or asset.get('original_filename') or ''}",
                   select=False)


# --------------------------------------------------------------------------- #
# Обзор проекта
# --------------------------------------------------------------------------- #


def overview(store: Any, project_id: int) -> dict:
    """Всё о проекте ИИ-видео: стиль, персонажи (облики, версии), локации, серии, бюджет, карточки."""
    from app import aivideo_gen as G

    project = require_project(store, project_id)
    style = get_style(store, project_id)
    refs = list_refs(store, project_id)
    by_target: dict[tuple[str, int], list[dict]] = {}
    for ref in refs:
        by_target.setdefault((ref["target_type"], int(ref["target_id"])), []).append(ref)
    characters = []
    for c in list_characters(store, project_id):
        c["refs"] = by_target.get(("character", int(c["id"])), [])
        for look in c["looks"]:
            look["refs"] = by_target.get(("look", int(look["id"])), [])
        characters.append(c)
    locations = []
    for loc in list_locations(store, project_id):
        loc["refs"] = by_target.get(("location", int(loc["id"])), [])
        locations.append(loc)
    return {
        "project": {k: project.get(k) for k in ("id", "name", "kind", "kind_label", "description")},
        "style": style, "style_refs": by_target.get(("style", 0), []),
        "characters": characters, "locations": locations, "scripts": list_scripts(store, project_id),
        "budget": G.budget_view(store, project_id), "approvals": G.stage_approvals(store, project_id),
        "jobs": G.job_counts(store, project_id),
    }


def script_view(store: Any, script_id: int) -> dict:
    """Серия с планами и версиями кадров/озвучки каждого плана (раскадровка)."""
    from app import aivideo_gen as G

    script = get_script(store, script_id)
    for shot in script["shots"]:
        shot["keyframes"] = list_refs(store, script["project_id"], "shot", int(shot["id"]), "keyframe")
        shot["videos"] = list_refs(store, script["project_id"], "shot", int(shot["id"]), "shot_video")
        shot["line_audio"] = list_refs(store, script["project_id"], "line", int(shot["id"]), "line_audio")
        spent = store.db.query_one(
            "SELECT COALESCE(SUM(l.amount_rub), 0) AS s FROM aiv_ledger l JOIN aiv_gen_jobs j ON j.id = l.job_id "
            "WHERE j.target_type IN ('shot', 'line') AND j.target_id = ?", (int(shot["id"]),))
        shot["spent_rub"] = round(float(spent["s"] or 0), 2) if spent else 0.0
    script["budget"] = G.budget_view(store, script["project_id"], script_id)
    script["approvals"] = G.stage_approvals(store, script["project_id"], script_id)
    return script
