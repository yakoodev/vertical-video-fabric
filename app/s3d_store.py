"""3D-серия в сервисе (проект вида ``series3d``): библиотека проекта, серии с версиями плана и задания рендера.

Владелец решил «3D в сервисе — всё кроме блендера»: серии, которые раньше собирали скрипты на компьютере
(``tools/vtubika_story.py``, ``tools/vtubika_vrm_clip.py``, ``tools/meme3d_pilot.py``), делаются из интерфейса,
агентом и через MCP. Здесь — только данные; план и его проверка — ``app/s3d_plan.py``, сборка VRM-сцены —
``app/s3d_vrm.py``, голос — ``app/s3d_voice.py``, задание рендера — ``app/s3d_render.py``.

* **Библиотека проекта** (``s3d_library``): персонажи (``character``: VRM-модель из наших или загруженная,
  перекраска материалов, наушники, голос; робот-помощник ``deskbot`` из мем-пилота; герой из примитивов ``voxel``),
  локации (``location``: нарисованная комната стримера ``plate`` со светом и экраном или комната-пресет ``room`` с
  палитрой), вещи (``prop``). Значения — JSON по виду (``normalize_item``).
* **Настройки по умолчанию** (``s3d_settings``): движок, стиль/свет/настроение (пресеты), голос рассказчика,
  музыка, субтитры, вступление и хвост.
* **Серия** (``s3d_episodes``) — план JSON; каждая правка — новая версия (``s3d_plan_versions``: кто и зачем).
* **Задание рендера** (``s3d_jobs``) — одна попытка собрать серию из версии плана: голос → 3D → проверки →
  холст-клип. Статусы — ``JOB_STATUSES``, ведёт ``app/s3d_render.advance``.
"""

from __future__ import annotations

import re
from typing import Any

from app.projects_core import _json, _loads, _text

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS s3d_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, kind, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS s3d_settings (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        data_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS s3d_episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL DEFAULT '',
        engine TEXT NOT NULL DEFAULT 'vrm_desk',
        status TEXT NOT NULL DEFAULT 'draft',
        plan_version INTEGER NOT NULL DEFAULT 0,
        plan_json TEXT NOT NULL DEFAULT '{}',
        source_json TEXT NOT NULL DEFAULT '{}',
        clip_id INTEGER,
        last_job_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_s3d_episodes_project ON s3d_episodes(project_id, id)",
    """
    CREATE TABLE IF NOT EXISTS s3d_plan_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER NOT NULL REFERENCES s3d_episodes(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        plan_json TEXT NOT NULL DEFAULT '{}',
        author TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (episode_id, version)
    )
    """,
    # Задание: queued → voice → (awaiting_approval) → render → done | failed | cancelled.
    """
    CREATE TABLE IF NOT EXISTS s3d_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER NOT NULL REFERENCES s3d_episodes(id) ON DELETE CASCADE,
        project_id INTEGER NOT NULL,
        plan_version INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        note TEXT NOT NULL DEFAULT '',
        progress REAL NOT NULL DEFAULT 0,
        options_json TEXT NOT NULL DEFAULT '{}',
        approval_id INTEGER,
        estimate_rub REAL NOT NULL DEFAULT 0,
        result_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '',
        requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TEXT,
        finished_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_s3d_jobs_status ON s3d_jobs(status, id)",
    "CREATE INDEX IF NOT EXISTS idx_s3d_jobs_episode ON s3d_jobs(episode_id, id)",
]

KINDS = ("character", "location", "prop")
KIND_LABELS = {"character": "персонаж", "location": "локация", "prop": "вещь"}
CHARACTER_TYPES = {"vrm": "VRM-персонаж (аниме-модель)", "deskbot": "робот-помощник из мем-пилота",
                   "voxel": "герой из примитивов"}
LOCATION_TYPES = {"plate": "нарисованная комната стримера (VRM за столом)", "room": "3D-комната из пресета"}
PROP_TYPES = {"plate_fg": "вещь на переднем плане комнаты стримера", "object": "предмет 3D-сцены"}
PLATE_PROPS = {"cup": "стакан лапши на столе", "mic": "микрофон на стойке", "cats": "кот на краю стола"}

JOB_STATUSES = ("queued", "voice", "awaiting_approval", "render", "done", "failed", "cancelled")
JOB_ACTIVE = ("queued", "voice", "awaiting_approval", "render")
JOB_LABELS = {"queued": "в очереди", "voice": "записываю голос", "awaiting_approval": "ждёт «Разрешить» за голос",
              "render": "рисую 3D и проверяю", "done": "готово", "failed": "не получилось", "cancelled": "отменено"}
EPISODE_LABELS = {"draft": "черновик", **JOB_LABELS}

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG = re.compile(r"[^a-z0-9_]+")


class S3dError(ValueError):
    """Неверные данные 3D-серии (текст — для человека, что поправить)."""


def ensure_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)
    from app import s3d_voice

    s3d_voice.register()


def slugify(value: Any, fallback: str = "item") -> str:
    from app.aivideo import slugify as _slug

    out = SLUG.sub("_", _slug(str(value or ""), fallback).replace("-", "_").lower()).strip("_")
    return (out or fallback)[:40]


def _hex(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not HEX.match(text):
        raise S3dError(f"{name}: цвет «#rrggbb», а не {value!r}")
    return text.lower()


def _colors(value: Any, name: str, pair: bool) -> dict:
    """Перекраска материалов: {"Hair": ["#тень", "#свет"]} (pair) или {"Hair": "#цвет"} (оттенок)."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise S3dError(f"{name}: объект {{материал: цвет}}")
    out = {}
    for mat, col in value.items():
        key = _text(mat, 40)
        if not key:
            continue
        if pair:
            if not isinstance(col, list) or len(col) != 2:
                raise S3dError(f"{name}.{key}: два цвета [тень, свет]")
            out[key] = [_hex(col[0], f"{name}.{key}"), _hex(col[1], f"{name}.{key}")]
        else:
            out[key] = _hex(col, f"{name}.{key}")
    return out


def _voice(value: Any) -> dict:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        raise S3dError("voice: {provider: gemini|polza|none, voice, model?, style?}")
    provider = str(value.get("provider") or "gemini").strip().lower()
    if provider not in ("gemini", "polza", "none"):
        raise S3dError("voice.provider: gemini (бесплатно), polza (платно, по карточке) или none (тон вместо речи)")
    return {"provider": provider, "voice": _text(value.get("voice"), 60), "model": _text(value.get("model"), 80),
            "style": _text(value.get("style"), 300)}


def normalize_character(data: dict) -> dict:
    from app import vtubika_vrm_stage as VS

    kind = str(data.get("type") or "vrm").strip().lower()
    if kind not in CHARACTER_TYPES:
        raise S3dError(f"персонаж: type — одно из {', '.join(CHARACTER_TYPES)}")
    out: dict[str, Any] = {"type": kind, "voice": _voice(data.get("voice")), "persona": _text(data.get("persona"), 400)}
    if kind == "vrm":
        v = dict(data.get("vrm") or {})
        base = str(v.get("base") or "nika").strip()
        if base not in VS.HEROINES and base not in VS.AVATARS:
            raise S3dError(f"vrm.base: одна из готовых моделей {', '.join([*VS.HEROINES, *VS.AVATARS])}")
        model = str(v.get("model") or "").strip()
        if model and (".." in model or not model.lower().endswith(".vrm")):
            raise S3dError("vrm.model: файл .vrm из папки моделей (или uploads/<имя>.vrm после загрузки)")
        hp = v.get("headphones")
        if isinstance(hp, dict):
            hp = {k: _hex(hp[k], f"vrm.headphones.{k}") for k in ("color", "accent") if hp.get(k)}
        elif hp is not None:
            hp = bool(hp)
        out["vrm"] = {"base": base, "model": model, "recolor": _colors(v.get("recolor"), "vrm.recolor", True),
                      "tint": _colors(v.get("tint"), "vrm.tint", False), "headphones": hp}
    elif kind == "deskbot":
        d = dict(data.get("deskbot") or {})
        out["deskbot"] = {k: _hex(d[k], f"deskbot.{k}") for k in ("body", "trim", "plate", "screen", "glow", "accent")
                          if d.get(k)}
    else:
        out["voxel"] = {k: v for k, v in dict(data.get("voxel") or {}).items() if isinstance(v, (str, int, float, bool))}
    return out


def normalize_location(data: dict) -> dict:
    from app import presets3d

    kind = str(data.get("type") or "plate").strip().lower()
    if kind not in LOCATION_TYPES:
        raise S3dError(f"локация: type — одно из {', '.join(LOCATION_TYPES)}")
    if kind == "plate":
        p = dict(data.get("plate") or {})
        light = {}
        for k, v in dict(p.get("light") or {}).items():
            if k in ("keyColor", "fill", "rim"):
                light[k] = _hex(v, f"plate.light.{k}")
            elif k in ("keyPower", "fillPower") and isinstance(v, (int, float)) and 0 <= float(v) <= 6:
                light[k] = round(float(v), 3)
        screen = p.get("screen") if isinstance(p.get("screen"), dict) else {}
        return {"type": "plate", "plate": {"base": "stream_room", "light": light, "screen": screen,
                                           "props": {k: bool(v) for k, v in dict(p.get("props") or {}).items() if k in PLATE_PROPS}}}
    r = dict(data.get("room") or {})
    preset = str(r.get("preset") or "").strip()
    if not presets3d.get("room", preset):
        raise S3dError(f"room.preset: одно из {', '.join(presets3d.names('room'))}")
    return {"type": "room", "room": {"preset": preset, "colors": {str(k): _hex(v, f"room.colors.{k}") for k, v in
                                                                  dict(r.get("colors") or {}).items()},
                                     "hide": [str(x) for x in r.get("hide") or []][:20],
                                     "scene": r.get("scene") if isinstance(r.get("scene"), dict) else None}}


def normalize_prop(data: dict) -> dict:
    kind = str(data.get("type") or "plate_fg").strip().lower()
    if kind not in PROP_TYPES:
        raise S3dError(f"вещь: type — одно из {', '.join(PROP_TYPES)}")
    if kind == "plate_fg":
        what = str(data.get("what") or "").strip()
        if what not in PLATE_PROPS:
            raise S3dError(f"вещь комнаты стримера: what — одно из {', '.join(PLATE_PROPS)}")
        return {"type": kind, "what": what}
    obj = data.get("object")
    if not isinstance(obj, dict) or not (obj.get("primitive") or obj.get("model")):
        raise S3dError("предмет 3D-сцены: object {primitive|model, params, position}")
    return {"type": kind, "object": obj}


NORMALIZE = {"character": normalize_character, "location": normalize_location, "prop": normalize_prop}


def normalize_item(kind: str, data: dict) -> dict:
    if kind not in KINDS:
        raise S3dError(f"вид: одно из {', '.join(KINDS)}")
    if not isinstance(data, dict):
        raise S3dError("данные — объект")
    return NORMALIZE[kind](data)


def _item_view(row: Any) -> dict:
    d = dict(row)
    data = _loads(d.pop("data_json", "{}"), {})
    return {**d, **data, "kind_label": KIND_LABELS.get(d["kind"], d["kind"])}


def list_items(store: Any, project_id: int, kind: str | None = None) -> list[dict]:
    if kind:
        rows = store.db.query_all("SELECT * FROM s3d_library WHERE project_id = ? AND kind = ? ORDER BY id",
                                  (int(project_id), kind))
    else:
        rows = store.db.query_all("SELECT * FROM s3d_library WHERE project_id = ? ORDER BY kind, id", (int(project_id),))
    return [_item_view(r) for r in rows]


def get_item(store: Any, project_id: int, kind: str, slug: str) -> dict:
    row = store.db.query_one("SELECT * FROM s3d_library WHERE project_id = ? AND kind = ? AND slug = ?",
                             (int(project_id), kind, str(slug)))
    if not row:
        raise KeyError(f"нет {KIND_LABELS.get(kind, kind)}а «{slug}» в библиотеке проекта")
    return _item_view(row)


def save_item(store: Any, project_id: int, kind: str, data: dict, slug: str | None = None, name: str | None = None) -> dict:
    """Новый элемент библиотеки или правка (тот же slug — замена данных)."""
    from app import projects as P

    P.get_project(store, int(project_id), include_sources=False)
    clean = normalize_item(kind, data)
    name = _text(name if name is not None else data.get("name"), 80)
    slug = slugify(slug or data.get("slug") or name, kind)
    store.db.execute(
        "INSERT INTO s3d_library (project_id, kind, slug, name, data_json) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(project_id, kind, slug) DO UPDATE SET name = excluded.name, data_json = excluded.data_json, "
        "updated_at = CURRENT_TIMESTAMP",
        (int(project_id), kind, slug, name or slug, _json(clean)))
    return get_item(store, project_id, kind, slug)


def delete_item(store: Any, project_id: int, kind: str, slug: str) -> bool:
    cur = store.db.execute("DELETE FROM s3d_library WHERE project_id = ? AND kind = ? AND slug = ?",
                           (int(project_id), kind, str(slug)))
    return bool(cur.rowcount)


def library(store: Any, project_id: int) -> dict:
    """Вся библиотека по видам: {characters: {slug: …}, locations: {…}, props: {…}} — для проверки плана."""
    out: dict[str, dict] = {"characters": {}, "locations": {}, "props": {}}
    for it in list_items(store, project_id):
        out[f"{it['kind']}s"][it["slug"]] = it
    return out


# --------------------------------------------------------------------------- готовые персонажи и локации

BUILTIN_NAMES = {"alina": "Алина", "vera": "Вера", "nika": "Ника", "zhenya": "Женя", "trial": "Пробная модель",
                 "vera_oc": "Персонаж Веры", "kira": "Кира", "zhenya_cam": "Женя на вебке"}


def builtin_items() -> list[tuple[str, str, str, dict]]:
    """(вид, slug, имя, данные) готовых: героини и аватары Витубики (VRM), робот Клод (мем-пилот), комната стримера."""
    from app import vtubika_vrm_stage as VS

    out: list[tuple[str, str, str, dict]] = []
    for slug in VS.HEROINES:
        out.append(("character", slug, BUILTIN_NAMES.get(slug, slug), {"type": "vrm", "vrm": {"base": slug}}))
    for slug in VS.AVATARS:
        out.append(("character", slug, BUILTIN_NAMES.get(slug, slug), {"type": "vrm", "vrm": {"base": slug}}))
    out.append(("character", "klod", "Робот Клод", {"type": "deskbot", "deskbot": {
        "body": "#d9774b", "trim": "#a9512f", "plate": "#f6e6cf", "screen": "#2a1f2d", "glow": "#fff1dc", "accent": "#ffc857"}}))
    out.append(("location", "stream_room", "Комната стримерши", {"type": "plate", "plate": {"props": {"cup": True, "mic": True, "cats": True}}}))
    out.append(("location", "stream_room_warm", "Комната стримерши, тёплый вечер", {"type": "plate", "plate": {
        "light": {"keyColor": "#ffe2c4", "keyPower": 2.2, "fill": "#a8644a", "fillPower": 0.7}, "props": {"cup": True, "mic": True}}}))
    for what, name in PLATE_PROPS.items():
        out.append(("prop", what, name[0].upper() + name[1:], {"type": "plate_fg", "what": what}))
    return out


def seed_builtins(store: Any, project_id: int, only: list[str] | None = None) -> list[dict]:
    """Готовые персонажи, локации и вещи в библиотеку проекта (что уже есть — не трогает)."""
    have = {(i["kind"], i["slug"]) for i in list_items(store, project_id)}
    added = []
    for kind, slug, name, data in builtin_items():
        if (kind, slug) in have or (only and slug not in only):
            continue
        added.append(save_item(store, project_id, kind, data, slug=slug, name=name))
    return added


# --------------------------------------------------------------------------- настройки проекта

DEFAULT_SETTINGS: dict[str, Any] = {
    "engine": "vrm_desk", "presets": {"look": "anime_soft"}, "narrator_voice": {}, "music": {},
    "captions": {"y": 0.8}, "lead": 0.6, "tail": 1.8,
}


def get_settings(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT data_json FROM s3d_settings WHERE project_id = ?", (int(project_id),))
    data = _loads(row["data_json"], {}) if row else {}
    return {**DEFAULT_SETTINGS, **data}


def save_settings(store: Any, project_id: int, fields: dict) -> dict:
    from app import presets3d
    from app import projects as P

    P.get_project(store, int(project_id), include_sources=False)
    cur = get_settings(store, project_id)
    for key, value in (fields or {}).items():
        if key == "engine":
            from app.s3d_plan import ENGINES

            if value not in ENGINES:
                raise S3dError(f"engine: одно из {', '.join(ENGINES)}")
            cur[key] = value
        elif key == "presets":
            block = {k: v for k, v in dict(value or {}).items() if k in ("look", "light", "mood", "fx")}
            try:
                presets3d.validate_presets(block)
            except presets3d.PresetError as exc:
                raise S3dError("presets: " + "; ".join(exc.problems)) from exc
            cur[key] = block
        elif key == "narrator_voice":
            cur[key] = _voice(value)
        elif key == "music":
            cur[key] = {k: value[k] for k in ("track_id", "ref", "under_db") if isinstance(value, dict) and value.get(k) is not None}
        elif key == "captions":
            y = float(dict(value or {}).get("y", 0.8))
            cur[key] = {"y": min(0.92, max(0.1, y))}
        elif key in ("lead", "tail"):
            cur[key] = min(5.0, max(0.0, float(value)))
    store.db.execute("INSERT INTO s3d_settings (project_id, data_json) VALUES (?, ?) ON CONFLICT(project_id) DO UPDATE "
                     "SET data_json = excluded.data_json, updated_at = CURRENT_TIMESTAMP", (int(project_id), _json(cur)))
    return get_settings(store, project_id)


# --------------------------------------------------------------------------- серии и версии плана


def _episode_view(store: Any, row: Any, full: bool = True) -> dict:
    d = dict(row)
    d["plan"] = _loads(d.pop("plan_json", "{}"), {}) if full else None
    d["source"] = _loads(d.pop("source_json", "{}"), {})
    d["status_label"] = EPISODE_LABELS.get(d["status"], d["status"])
    if not full:
        d.pop("plan", None)
    return d


def get_episode(store: Any, episode_id: int, full: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM s3d_episodes WHERE id = ?", (int(episode_id),))
    if not row:
        raise KeyError(f"нет 3D-серии {episode_id}")
    return _episode_view(store, row, full)


def list_episodes(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM s3d_episodes WHERE project_id = ? ORDER BY id DESC", (int(project_id),))
    return [_episode_view(store, r, full=False) for r in rows]


def create_episode(store: Any, project_id: int, title: str, plan: dict, *, author: str = "agent", note: str = "",
                   source: dict | None = None) -> dict:
    """Новая серия с первой версией плана (план уже проверен ``app.s3d_plan.validate_plan``)."""
    from app import projects as P

    P.get_project(store, int(project_id), include_sources=False)
    with store.db.transaction():
        cur = store.db.execute(
            "INSERT INTO s3d_episodes (project_id, title, engine, plan_version, plan_json, source_json) VALUES (?, ?, ?, 1, ?, ?)",
            (int(project_id), _text(title or plan.get("title"), 120) or "Новая серия", str(plan.get("engine") or "vrm_desk"),
             _json(plan), _json(source or {})))
        eid = int(cur.lastrowid)
        store.db.execute("INSERT INTO s3d_plan_versions (episode_id, version, plan_json, author, note) VALUES (?, 1, ?, ?, ?)",
                         (eid, _json(plan), _text(author, 40), _text(note, 300)))
    return get_episode(store, eid)


def save_plan(store: Any, episode_id: int, plan: dict, *, author: str = "agent", note: str = "") -> dict:
    """Новая версия плана (проверенного). Тот же план второй раз — версия не растёт."""
    ep = get_episode(store, episode_id)
    if _json(ep["plan"]) == _json(plan):
        return ep
    version = int(ep["plan_version"]) + 1
    with store.db.transaction():
        store.db.execute("INSERT INTO s3d_plan_versions (episode_id, version, plan_json, author, note) VALUES (?, ?, ?, ?, ?)",
                         (int(episode_id), version, _json(plan), _text(author, 40), _text(note, 300)))
        store.db.execute("UPDATE s3d_episodes SET plan_json = ?, plan_version = ?, title = ?, engine = ?, "
                         "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (_json(plan), version, _text(plan.get("title") or ep["title"], 120), str(plan.get("engine") or ep["engine"]),
                          int(episode_id)))
    return get_episode(store, episode_id)


def plan_versions(store: Any, episode_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id, version, author, note, created_at FROM s3d_plan_versions WHERE episode_id = ? "
                              "ORDER BY version DESC", (int(episode_id),))
    return [dict(r) for r in rows]


def plan_at(store: Any, episode_id: int, version: int) -> dict:
    row = store.db.query_one("SELECT plan_json FROM s3d_plan_versions WHERE episode_id = ? AND version = ?",
                             (int(episode_id), int(version)))
    if not row:
        raise KeyError(f"у серии {episode_id} нет версии плана {version}")
    return _loads(row["plan_json"], {})


def delete_episode(store: Any, episode_id: int) -> bool:
    return bool(store.db.execute("DELETE FROM s3d_episodes WHERE id = ?", (int(episode_id),)).rowcount)


# --------------------------------------------------------------------------- задания рендера


def _job_view(row: Any) -> dict:
    d = dict(row)
    d["options"] = _loads(d.pop("options_json", "{}"), {})
    d["result"] = _loads(d.pop("result_json", "{}"), {})
    d["status_label"] = JOB_LABELS.get(d["status"], d["status"])
    return d


def get_job(store: Any, job_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM s3d_jobs WHERE id = ?", (int(job_id),))
    if not row:
        raise KeyError(f"нет задания 3D-рендера {job_id}")
    return _job_view(row)


def episode_jobs(store: Any, episode_id: int, limit: int = 10) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM s3d_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT ?",
                              (int(episode_id), int(limit)))
    return [_job_view(r) for r in rows]


def active_job(store: Any, episode_id: int) -> dict | None:
    marks = ", ".join("?" for _ in JOB_ACTIVE)
    row = store.db.query_one(f"SELECT * FROM s3d_jobs WHERE episode_id = ? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
                             (int(episode_id), *JOB_ACTIVE))
    return _job_view(row) if row else None


def active_jobs(store: Any) -> list[int]:
    marks = ", ".join("?" for _ in JOB_ACTIVE)
    return [int(r["id"]) for r in store.db.query_all(f"SELECT id FROM s3d_jobs WHERE status IN ({marks}) ORDER BY id", JOB_ACTIVE)]


def create_job(store: Any, episode_id: int, options: dict | None = None, requested_by: str = "agent") -> dict:
    ep = get_episode(store, episode_id, full=False)
    with store.db.transaction():
        cur = store.db.execute("INSERT INTO s3d_jobs (episode_id, project_id, plan_version, options_json, requested_by) "
                               "VALUES (?, ?, ?, ?, ?)", (int(episode_id), int(ep["project_id"]), int(ep["plan_version"]),
                                                         _json(options or {}), _text(requested_by, 40)))
        jid = int(cur.lastrowid)
        store.db.execute("UPDATE s3d_episodes SET status = 'queued', last_job_id = ?, updated_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (jid, int(episode_id)))
    return get_job(store, jid)


def set_job(store: Any, job_id: int, status: str | None = None, expect: tuple[str, ...] | None = None, **fields: Any) -> bool:
    """Смена статуса «сравнить-и-записать» (``expect`` — из каких статусов можно); серия получает тот же статус."""
    sets, params = ["updated_at = CURRENT_TIMESTAMP"], []
    if status is not None:
        if status not in JOB_STATUSES:
            raise S3dError(f"статус задания: {status}")
        sets.append("status = ?")
        params.append(status)
        if status == "render":
            sets.append("started_at = COALESCE(started_at, CURRENT_TIMESTAMP)")
        if status in ("done", "failed", "cancelled"):
            sets.append("finished_at = CURRENT_TIMESTAMP")
    for key, value in fields.items():
        if key in ("result", "options"):
            sets.append(f"{key}_json = ?")
            params.append(_json(value))
        elif key in ("note", "error", "progress", "approval_id", "estimate_rub"):
            sets.append(f"{key} = ?")
            params.append(value)
        else:
            raise S3dError(f"поле задания {key}")
    where, wparams = "id = ?", [int(job_id)]
    if expect:
        where += f" AND status IN ({', '.join('?' for _ in expect)})"
        wparams += list(expect)
    cur = store.db.execute(f"UPDATE s3d_jobs SET {', '.join(sets)} WHERE {where}", (*params, *wparams))
    if not cur.rowcount:
        return False
    if status is not None:
        job = get_job(store, job_id)
        extra = ", clip_id = ?" if status == "done" and (job["result"] or {}).get("clip_id") else ""
        args: list[Any] = [status]
        if extra:
            args.append(int(job["result"]["clip_id"]))
        store.db.execute(f"UPDATE s3d_episodes SET status = ?{extra}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND last_job_id = ?",
                         (*args, int(job["episode_id"]), int(job_id)))
    return True
