"""Навигация Проект → Видео → Клип: человеческое имя видео и лёгкие счётчики для боковой панели.

Схему базы не трогает. Всё считается одним-двумя запросами на экран, без ffprobe и без файлов клипов:
панель и лестница опрашивают это часто, а Apex — это 200+ загрузок.

Имя видео (``video_title``) — по убыванию надёжности:

1. имя, которое дал владелец (``original_filename``, если оно не техническое);
2. название VOD, которое видел наблюдатель (``vod_watch_items.title``);
3. общее имя кусков стрима: у «Мура Minecraft 1/4, 2/4…» видео называется «Мура Minecraft»;
4. заголовок из тегов файла (ffprobe ``format.tags.title``);
5. сезон и серия, дата записи или единственный клип — что видно из технического имени файла;
6. вид и дата: «Стрим 31.10» (дата из тегов файла, если нет — день добавления).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from app.store import source_file_present

_EXT = re.compile(r"\.(mp4|mkv|webm|mov|avi|m4v|ts|flv)$", re.I)
_TWITCH = re.compile(r"^twitch-\d+$", re.I)
_HASH = re.compile(r"^[0-9a-f]{16,}$", re.I)
_EPISODE = re.compile(r"^[a-z]+-\d+-s(\d+)-e(\d+)(?:-(.+))?$", re.I)
_RECORD_DATE = re.compile(r"(\d{4})[.\-_](\d{2})[.\-_](\d{2})\s*-\s*(\d{2})[.\-_](\d{2})")
_TAKE = re.compile(r"^v\d+_\d+(_[a-z])?$", re.I)
_GENERIC = re.compile(r"^(video|source|clip|upload|download|file|запись|видео)?[\s_\-]*\d*$", re.I)
_PART = re.compile(r"^(.+?)\s+\d+\s*/\s*\d+\s*$")
_TAKE_PREFIX = re.compile(r"^[A-Za-zА-Яа-я]{0,2}\d+\s+v\d+:\s*")

KIND_LABEL = {
    "twitch_url": "Стрим",
    "youtube_url": "YouTube",
    "smotvibe_url": "Серия",
    "upload": "Запись",
    "direct_url": "Видео",
    "canvas": "Холст",
    "clip_upload": "Клип",
}

# Скрытые служебные источники (app/store.py: _HIDDEN_SOURCE_TYPES): в списках видео их нет.
_HIDDEN = "('clip_upload', 'canvas')"


def _stem(filename: str) -> str:
    return _EXT.sub("", str(filename or "").strip()).strip()


def is_technical_name(filename: Any) -> bool:
    """Имя выдал загрузчик или камера, а не человек: показывать его как название нельзя."""
    stem = _stem(str(filename or ""))
    if not stem or "://" in stem:
        return True
    return bool(
        _TWITCH.match(stem)
        or _HASH.match(stem)
        or _EPISODE.match(stem)
        or _TAKE.match(stem)
        or _GENERIC.match(stem)
        or _RECORD_DATE.search(stem)
    )


def _short_date(value: Any) -> str:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{match.group(3)}.{match.group(2)}" if match else ""


def _part_name(titles: Iterable[str]) -> str:
    names = Counter()
    for title in titles:
        match = _PART.match(str(title or "").strip())
        if match:
            names[match.group(1).strip()] += 1
    return names.most_common(1)[0][0] if names else ""


def video_title(
    source: Mapping[str, Any],
    *,
    plan_titles: Iterable[str] = (),
    vod_title: str = "",
    tag_title: str = "",
    tag_date: str = "",
) -> str:
    """Название видео для человека. ``plan_titles`` нужны только для технического имени файла."""
    filename = str(source.get("original_filename") or "")
    stem = _stem(filename)
    if stem and not is_technical_name(filename):
        return stem.replace("_", " ") if " " not in stem else stem
    if vod_title.strip():
        return vod_title.strip()
    titles = [str(t or "").strip() for t in plan_titles if str(t or "").strip()]
    part = _part_name(titles)
    if part:
        return part
    if tag_title.strip():
        return tag_title.strip()
    episode = _EPISODE.match(stem)
    if episode:
        season, number, voice = episode.groups()
        return f"Сезон {int(season)}, серия {int(number)}" + (f" ({voice.replace('_', ' ')})" if voice else "")
    recorded = _RECORD_DATE.search(stem)
    if recorded:
        year, month, day, hour, minute = recorded.groups()
        return f"Запись {day}.{month}.{year}, {hour}:{minute}"
    distinct = {_TAKE_PREFIX.sub("", t) for t in titles}
    if len(distinct) == 1:
        return distinct.pop()
    kind = KIND_LABEL.get(str(source.get("source_type") or ""), "Видео")
    date = _short_date(tag_date) or _short_date(source.get("created_at"))
    return f"{kind} {date}".strip()


def _chunks(ids: list[int], size: int = 400) -> Iterable[list[int]]:
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def titles_for(store: Any, sources: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """Имена пачки видео за несколько запросов; подсказки читаем только у технических имён."""
    rows = list(sources)
    need = [int(s["id"]) for s in rows if is_technical_name(s.get("original_filename"))]
    plans: dict[int, list[str]] = {}
    vods: dict[int, str] = {}
    tags: dict[int, tuple[str, str]] = {}
    for chunk in _chunks(need):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT source_id, title FROM clip_plans WHERE source_id IN ({marks}) ORDER BY id", chunk
        ):
            plans.setdefault(int(row["source_id"]), []).append(row["title"])
        for row in store.db.query_all(
            f"SELECT source_id, title FROM vod_watch_items WHERE title != '' AND source_id IN ({marks})", chunk
        ):
            vods[int(row["source_id"])] = str(row["title"])
        for row in store.db.query_all(
            f"""
            SELECT id,
                   json_extract(metadata_json, '$.format.tags.title') AS tag_title,
                   json_extract(metadata_json, '$.format.tags.creation_time') AS tag_date
            FROM sources WHERE id IN ({marks}) AND json_valid(metadata_json)
            """,
            chunk,
        ):
            tags[int(row["id"])] = (str(row["tag_title"] or ""), str(row["tag_date"] or ""))
    return {
        int(s["id"]): video_title(
            s,
            plan_titles=plans.get(int(s["id"]), ()),
            vod_title=vods.get(int(s["id"]), ""),
            tag_title=tags.get(int(s["id"]), ("", ""))[0],
            tag_date=tags.get(int(s["id"]), ("", ""))[1],
        )
        for s in rows
    }


def with_titles(store: Any, sources: list[dict]) -> list[dict]:
    """Источники с полем ``display_name`` (для /api/sources)."""
    names = titles_for(store, sources)
    return [{**s, "display_name": names.get(int(s["id"]), "")} for s in sources]


def _module_on(module_id: str, store: Any) -> bool:
    from app import modules

    return modules.is_enabled(module_id, store)


def _generated_plan_ids(store: Any, project_id: int | None = None) -> set[int]:
    """Клипы-итоги генераций: они показаны строкой своего ИИ-видео, истории или объяснялки."""
    where = "" if project_id is None else " AND project_id = ?"
    args: tuple = () if project_id is None else (int(project_id),)
    ids: set[int] = set()
    for table, column in (("aiv_scripts", "clip_plan_id"), ("story_episodes", "clip_id"), ("expl_videos", "clip_id")):
        for row in store.db.query_all(f"SELECT {column} AS id FROM {table} WHERE {column} IS NOT NULL{where}", args):
            ids.add(int(row["id"]))
    return ids


def nav_counts(store: Any) -> dict:
    """Всё для боковой панели одним ответом: проекты со счётчиком видео и бейдж «Входящие»."""
    def grouped(sql: str) -> dict[int, int]:
        return {int(r["project_id"]): int(r["n"]) for r in store.db.query_all(sql)}

    sources = grouped(
        f"SELECT project_id, COUNT(*) AS n FROM sources WHERE project_id IS NOT NULL "
        f"AND source_type NOT IN {_HIDDEN} GROUP BY project_id"
    )
    generated = [
        grouped(f"SELECT project_id, COUNT(*) AS n FROM {table} GROUP BY project_id")
        for table in ("aiv_scripts", "story_episodes", "expl_videos")
    ]
    taken = _generated_plan_ids(store)
    canvases: dict[int, int] = {}
    for row in store.db.query_all("SELECT clip_plan_id, project_id FROM plan_projects"):
        if int(row["clip_plan_id"]) not in taken:
            canvases[int(row["project_id"])] = canvases.get(int(row["project_id"]), 0) + 1

    open_questions: dict[int, int] = {}
    if _module_on("stream_questions", store):
        for table in ("stream_questions", "meme_moments"):
            for row in store.db.query_all(
                f"SELECT s.project_id, COUNT(*) AS n FROM {table} x JOIN sources s ON s.id = x.source_id "
                "WHERE x.status = 'open' AND s.project_id IS NOT NULL GROUP BY s.project_id"
            ):
                open_questions[int(row["project_id"])] = open_questions.get(int(row["project_id"]), 0) + int(row["n"])

    projects = []
    for row in store.db.query_all("SELECT id, name, kind FROM projects ORDER BY id"):
        pid = int(row["id"])
        projects.append({
            "id": pid,
            "name": row["name"],
            "kind": row["kind"],
            "videos": sources.get(pid, 0) + sum(g.get(pid, 0) for g in generated) + canvases.get(pid, 0),
            "open_questions": open_questions.get(pid, 0),
        })
    no_project = store.db.query_one(
        f"SELECT COUNT(*) AS n FROM sources WHERE project_id IS NULL AND source_type NOT IN {_HIDDEN}"
    )

    approvals = questions = memes = 0
    if _module_on("autopilot", store):
        row = store.db.query_one("SELECT COUNT(*) AS n FROM approvals WHERE status = 'pending'")
        approvals = int(row["n"]) if row else 0
    if _module_on("stream_questions", store):
        questions = int(store.db.query_one("SELECT COUNT(*) AS n FROM stream_questions WHERE status = 'open'")["n"])
        memes = int(store.db.query_one("SELECT COUNT(*) AS n FROM meme_moments WHERE status = 'open'")["n"])
    return {
        "projects": projects,
        "no_project_videos": int(no_project["n"]) if no_project else 0,
        "inbox": {"approvals": approvals, "questions": questions, "memes": memes,
                  "total": approvals + questions + memes},
    }


def _clip_formats(store: Any, source_ids: list[int]) -> dict[int, dict]:
    """На видео: клипов всего и сколько готово по форматам (последний готовый файл каждого клипа)."""
    out: dict[int, dict] = {}
    for chunk in _chunks(source_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"""
            SELECT cp.source_id, cp.id,
                   (SELECT c.id FROM clips c WHERE c.clip_plan_id = cp.id AND c.status = 'succeeded'
                     ORDER BY c.id DESC LIMIT 1) AS clip_id,
                   (SELECT c.width FROM clips c WHERE c.clip_plan_id = cp.id AND c.status = 'succeeded'
                     ORDER BY c.id DESC LIMIT 1) AS width,
                   (SELECT c.height FROM clips c WHERE c.clip_plan_id = cp.id AND c.status = 'succeeded'
                     ORDER BY c.id DESC LIMIT 1) AS height
            FROM clip_plans cp
            WHERE cp.source_id IN ({marks}) AND COALESCE(cp.hidden, 0) = 0
            """,
            chunk,
        ):
            item = out.setdefault(int(row["source_id"]), {"clips": 0, "vertical": 0, "horizontal": 0,
                                                          "no_render": 0, "thumb_clip_id": None})
            item["clips"] += 1
            if not row["clip_id"]:
                item["no_render"] += 1
                continue
            width, height = int(row["width"] or 0), int(row["height"] or 0)
            if width > height:
                item["horizontal"] += 1
            else:
                item["vertical"] += 1
            if not item["thumb_clip_id"] or int(row["clip_id"]) > item["thumb_clip_id"]:
                item["thumb_clip_id"] = int(row["clip_id"])
    return out


def _source_rows(store: Any, project_id: int | None) -> list[dict]:
    where = "s.project_id IS NULL" if project_id is None else "s.project_id = ?"
    args: tuple = () if project_id is None else (int(project_id),)
    return [dict(r) for r in store.db.query_all(
        f"""
        SELECT s.id, s.status, s.source_type, s.original_filename, s.original_url, s.local_path, s.duration_sec,
               s.width, s.height, s.created_at, s.project_id,
               (SELECT sc.message_count FROM source_chat sc WHERE sc.source_id = s.id) AS chat
        FROM sources s
        WHERE {where} AND s.source_type NOT IN {_HIDDEN}
        ORDER BY s.id DESC
        """,
        args,
    )]


def project_videos(store: Any, project_id: int | None) -> dict:
    """Видео проекта лёгким списком: лестница, вкладка «Видео». ``project_id=None`` — вне проектов."""
    rows = _source_rows(store, project_id)
    names = titles_for(store, rows)
    formats = _clip_formats(store, [int(r["id"]) for r in rows])
    videos = []
    for row in rows:
        sid = int(row["id"])
        videos.append({
            "id": sid,
            "name": names.get(sid) or f"Видео #{sid}",
            "kind": KIND_LABEL.get(str(row["source_type"]), "Видео"),
            "source_type": row["source_type"],
            "original_filename": row["original_filename"],
            "status": row["status"],
            "duration_sec": row["duration_sec"] or 0,
            "width": row["width"] or 0,
            "height": row["height"] or 0,
            "date": row["created_at"],
            "project_id": row["project_id"],
            "has_file": source_file_present(row["local_path"]),
            "chat": int(row["chat"] or 0),
            **formats.get(sid, {"clips": 0, "vertical": 0, "horizontal": 0, "no_render": 0, "thumb_clip_id": None}),
        })
    generated: list[dict] = []
    if project_id is not None:
        pid = int(project_id)
        for row in store.db.query_all(
            "SELECT id, title, status, created_at, clip_plan_id AS plan FROM aiv_scripts WHERE project_id = ? "
            "ORDER BY id DESC", (pid,),
        ):
            generated.append({"key": f"aiv-{row['id']}", "kind": "ИИ-видео", "tab": "aiv", "name": row["title"],
                              "status": row["status"], "date": row["created_at"], "plan_id": row["plan"]})
        for row in store.db.query_all(
            "SELECT id, title, premise, status, created_at, clip_id AS plan FROM story_episodes WHERE project_id = ? "
            "ORDER BY id DESC", (pid,),
        ):
            generated.append({"key": f"story-{row['id']}", "kind": "История", "tab": "story",
                              "name": row["title"] or row["premise"], "status": row["status"],
                              "date": row["created_at"], "plan_id": row["plan"]})
        for row in store.db.query_all(
            "SELECT id, title, topic, status, created_at, clip_id AS plan FROM expl_videos WHERE project_id = ? "
            "ORDER BY id DESC", (pid,),
        ):
            generated.append({"key": f"expl-{row['id']}", "kind": "Объяснялка", "tab": "expl",
                              "name": row["title"] or row["topic"], "status": row["status"],
                              "date": row["created_at"], "plan_id": row["plan"]})
        taken = _generated_plan_ids(store, pid)
        for row in store.db.query_all(
            """
            SELECT cp.id, cp.title, cp.source_id, cp.created_at, cp.status
            FROM plan_projects pp JOIN clip_plans cp ON cp.id = pp.clip_plan_id
            WHERE pp.project_id = ? ORDER BY cp.id DESC
            """,
            (pid,),
        ):
            if int(row["id"]) in taken:
                continue
            generated.append({"key": f"canvas-{row['id']}", "kind": "Холст", "tab": "", "name": row["title"],
                              "status": row["status"], "date": row["created_at"], "plan_id": row["id"],
                              "source_id": row["source_id"]})
        plans = [int(g["plan_id"]) for g in generated if g.get("plan_id")]
        if plans:
            located: dict[int, int] = {}
            for chunk in _chunks(plans):
                marks = ",".join("?" for _ in chunk)
                for row in store.db.query_all(f"SELECT id, source_id FROM clip_plans WHERE id IN ({marks})", chunk):
                    located[int(row["id"])] = int(row["source_id"])
            for item in generated:
                if item.get("plan_id"):
                    item["source_id"] = located.get(int(item["plan_id"]))
    return {"videos": videos, "generated": generated}


