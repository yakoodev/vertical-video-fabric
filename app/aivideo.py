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

Где что лежит. Здесь — схема и обзор проекта. Вынесено: стиль, персонажи, облики, локации ``app/aivideo_cast.py``, сценарии и планы ``app/aivideo_shots.py``,
референсы ``app/aivideo_refs.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any

from app import projects as P

from app.aivideo_cast import (  # noqa: F401
    STYLE_KINDS, STYLE_LABELS, QUALITIES, QUALITY_LABELS, PROVIDERS, ORIGINS, SPEECH_MODES, SFX_REF_RE, SLUG_RE,
    TRANSLIT, AivError, _loads, _json, slugify, _hexes, _now_sql, STYLE_FIELDS, VIDEO_RESOLUTIONS, STAND_INS,
    OWNER_FIELDS, require_project, _style_row, get_style, update_style, _catalog_models, _voice, _speech, _stand_in,
    _unique_slug, get_character, character_by_slug, list_characters, save_character, delete_character, save_look,
    delete_look, get_look, get_location, location_by_slug, list_locations, save_location, delete_location,
    _shot_from_row, mark_stale, _project_shots,
)
from app.aivideo_shots import (  # noqa: F401
    SCRIPT_STATUSES, CAMERA_MOVES, CAMERA_MOVE_ALIASES, SHOT_SIZES, TRANSITIONS, SHOT_MIN_SEC, SHOT_MAX_SEC,
    MAX_SHOTS, MAX_CAST, MAX_LINES_PER_SHOT, CHARS_PER_SEC, get_script, list_scripts, create_script,
    _duration_target, list_shots, STATE_FIELDS, STATE_LABELS, STATE_WATCH, MAX_PROPS, normalize_state, state_text,
    prop_counts, continuity_warnings, end_state_of, get_shot, camera_move, normalize_shot, _window, _is_sfx,
    replace_shots, update_script, update_shot, delete_script,
)
from app.aivideo_refs import (  # noqa: F401
    ref_view, get_ref, list_refs, selected_ref, add_ref, select_ref, set_ref_score, add_upload_ref,
)

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


# Колонки, добавленные после первых запусков (старые базы получают их ALTER TABLE).
AIV_COLUMNS = [
    # Кто взял запуск (поток@pid): взять можно только approved → submitting одним UPDATE.
    ("aiv_gen_jobs", "worker_id", "TEXT NOT NULL DEFAULT ''"),
    # Как персонаж «говорит»: voice — голос TTS, sfx — звуки (писк робота: записи пака по эмоции
    # реплики), none — молчит. speech_captions — показывать текст его sfx-реплик субтитрами.
    ("aiv_characters", "speech", "TEXT NOT NULL DEFAULT 'voice'"),
    ("aiv_characters", "speech_captions", "INTEGER NOT NULL DEFAULT 1"),
    # Кадр не похож на канон (проверка постоянства < 6) — показать владельцу, не перерисовывать само.
    ("aiv_refs", "needs_review", "INTEGER NOT NULL DEFAULT 0"),
    # v2 (видео планов и постоянство склеек).
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
    # Время отправки POST (UTC ISO), пишется ДО запроса: по нему адаптер ищет запрос, ушедший без
    # ответа (у Polza — /history/generations), в том числе после перезапуска.
    ("aiv_gen_jobs", "submitted_at", "TEXT NOT NULL DEFAULT ''"),
    # v3 (пары ключевых кадров, блокинг). Состояние на КОНЕЦ плана — его рисует конечный кадр
    # (kind endframe); у сцепленного следующего плана оно же — его старт.
    ("aiv_shots", "end_state_json", "TEXT NOT NULL DEFAULT '{}'"),
    # Блокинг плана: {spec — простая 3D-раскладка (комната, мебель, манекены, камера, start/end),
    # source gemini|auto|agent, scenes {start?, end?} — ручная JSON-сцена движка поверх spec}.
    ("aiv_shots", "blocking_json", "TEXT NOT NULL DEFAULT ''"),
    # Физические ограничения персонажа (дословно в промпты кадров и видео и в проверку видео):
    # «плоский диск, без рук/ног/лица, кроме глаз-экрана, всегда на полу, ровно один».
    ("aiv_characters", "constraints", "TEXT NOT NULL DEFAULT ''"),
    # Манекен блокинга: human | disc | quadruped | small (пусто — по карте личности).
    ("aiv_characters", "stand_in", "TEXT NOT NULL DEFAULT ''"),
    # Раскладка локации для блокинга — одна на все её планы (app/aivideo_layout.py): комната и вещи
    # из описания / с плиты (Gemini-зрение), {room, objects, source, key}; ключ — описание + плита.
    ("aiv_locations", "layout_json", "TEXT NOT NULL DEFAULT ''"),
]


def ensure_aiv_schema(conn) -> None:
    """Таблицы ИИ-видео (из ``Database.init`` после проектов и автопилота)."""
    for sql in AIV_SCHEMA_SQL:
        conn.execute(sql)
    for table, column, ddl in AIV_COLUMNS:
        if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    # До 02.11 отказ провайдера без суммы в ответе писался в траты по оценке, хотя за упавшую задачу
    # провайдер не списывает: такие строки журнала — 0 ₽ (серия и месяц не упираются в потолок).
    # Повторный запуск ничего не меняет (amount_rub > 0).
    refused = ("source = 'estimate' AND amount_rub > 0 AND note LIKE 'отказ провайдера%' AND job_id IN "
               "(SELECT id FROM aiv_gen_jobs WHERE status IN ('failed', 'blocked_policy'))")
    conn.execute(f"UPDATE aiv_gen_jobs SET cost_rub = 0, cost_source = 'none' WHERE id IN "
                 f"(SELECT job_id FROM aiv_ledger WHERE {refused})")
    conn.execute(f"UPDATE aiv_ledger SET amount_rub = 0, note = substr(note || ' — снято 02.11: за отказ провайдер "
                 f"не списывает', 1, 300) WHERE {refused}")
    # Карточка этапа (kind other, action aiv_budget) — свой обработчик решения владельца.
    import app.aivideo_gen  # noqa: F401


# --------------------------------------------------------------------------- #
# Константы
# --------------------------------------------------------------------------- #

PROJECT_KIND = "ai_video"


SPEECH_LABELS = {"voice": "говорит голосом", "sfx": "звуки вместо слов", "none": "молчит"}


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
    review = []
    video_review = []
    shots = script["shots"]
    for n, shot in enumerate(shots):
        shot["keyframes"] = list_refs(store, script["project_id"], "shot", int(shot["id"]), "keyframe")
        shot["endframes"] = list_refs(store, script["project_id"], "shot", int(shot["id"]), "endframe")
        shot["videos"] = list_refs(store, script["project_id"], "shot", int(shot["id"]), "shot_video")
        # Пара ключевых кадров плана: старт (свой или общий — конец прошлого плана) и конец.
        start, shared = G.start_frame_ref(store, int(script["project_id"]), shots, n)
        shot["start_frame"] = start
        shot["start_shared_from"] = shared
        shot["end_frame"] = G.end_frame_ref(store, int(script["project_id"]), shot)
        shot["end_state_effective"] = end_state_of(shot, shots[n + 1] if n + 1 < len(shots) else None)
        shot["blocking_frames"] = {w: selected_ref(store, script["project_id"], "shot", int(shot["id"]),
                                                   f"blocking_{w}") for w in ("start", "end")}
        vid = next((v for v in shot["videos"] if v["selected"]), None)
        qa = (vid or {}).get("score") or None
        shot["video_qa"] = qa if qa and qa.get("kind") == "video_qa" else None
        if vid and vid["needs_review"] and shot["video_qa"]:
            video_review.append({"shot_id": shot["id"], "idx": shot["idx"], "ref_id": vid["id"],
                                 "issues": shot["video_qa"].get("issues") or [],
                                 "note": shot["video_qa"].get("note") or ""})
        sel = next((k for k in shot["keyframes"] if k["selected"]), shot["keyframes"][-1] if shot["keyframes"] else None)
        # Выбранный кадр не похож на канон (проверка постоянства < 6): владелец решает, перерисовать ли.
        shot["needs_review"] = bool(sel and sel["needs_review"])
        if shot["needs_review"]:
            low = [c for c in ((sel.get("score") or {}).get("characters") or []) if c.get("score", 10) < 6]
            shot["review_note"] = "; ".join(f"{c['id']} {c['score']:g}/10: {c['differences']}" for c in low)
            review.append({"shot_id": shot["id"], "idx": shot["idx"], "ref_id": sel["id"],
                           "note": shot["review_note"]})
        shot["line_audio"] = list_refs(store, script["project_id"], "line", int(shot["id"]), "line_audio")
        spent = store.db.query_one(
            "SELECT COALESCE(SUM(l.amount_rub), 0) AS s FROM aiv_ledger l JOIN aiv_gen_jobs j ON j.id = l.job_id "
            "WHERE j.target_type IN ('shot', 'line') AND j.target_id = ?", (int(shot["id"]),))
        shot["spent_rub"] = round(float(spent["s"] or 0), 2) if spent else 0.0
    script["budget"] = G.budget_view(store, script["project_id"], script_id)
    script["approvals"] = G.stage_approvals(store, script["project_id"], script_id)
    script["needs_review"] = review
    script["video_review"] = video_review
    return script
