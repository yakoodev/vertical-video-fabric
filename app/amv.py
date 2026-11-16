"""AMV (аниме-клип под песню) как работа сервиса: карточка «Новое видео» → чат проекта с плейбуком amv и первым заданием.

Раньше AMV делал только наш агент руками инструментом ``tools/amv`` (так сделан одобренный «Человек-бензопила»,
D:/vvf-offload/amv/out_csm). Теперь:

* ``start(store, project_id, …)`` — исходники (видео проекта: фильм, серии, официальные трейлеры одного тайтла),
  песня (трек музыки или ссылка), сюжет от владельца (необязательно), формат. Заводится работа AMV (папка
  ``<runtime>/amv/<amv_id>`` с ``amv.json``), переписка чата проекта с закреплённым плейбуком ``amv``
  (``chat_threads.playbook``) и первое задание; агент начинает сразу.
* Шаги — инструменты агента (app/amv_tools.py): ``amv_analyze_song`` → ``amv_index_sources`` → ``amv_plan`` →
  ``amv_render`` (+ ``amv_status``); фоновые задания — app/amv_job.py.

Уроки владельца (плейбук docs/playbooks/amv.md): ОДИН тайтл с сюжетной аркой (смесь несвязанных фильмов — «нихуя не
понятно»), только официальные источники, песня — от владельца или из референса.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

PLAYBOOK = "amv"
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
TEST_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720)}
MAX_NOTES = 1500
ID_RE = re.compile(r"^amv[0-9a-f]{8}$")

_LOCK = threading.RLock()


# --------------------------------------------------------------------------- #
# Работа AMV: папка и amv.json
# --------------------------------------------------------------------------- #


def root() -> Path:
    from app.settings import settings

    return Path(settings.runtime_dir) / "amv"


def work_dir(amv_id: str) -> Path:
    key = str(amv_id or "").strip().lower()
    if not ID_RE.match(key):
        raise KeyError(f"нет работы AMV {amv_id!r} (вид amvXXXXXXXX)")
    return root() / key


def load(amv_id: str) -> dict:
    path = work_dir(amv_id) / "amv.json"
    if not path.is_file():
        raise KeyError(f"нет работы AMV {amv_id}")
    with _LOCK:
        return json.loads(path.read_text(encoding="utf-8"))


def save(manifest: dict) -> dict:
    folder = work_dir(manifest["id"])
    folder.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = round(time.time(), 1)
    tmp = folder / "amv.json.part"
    with _LOCK:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(folder / "amv.json")
    return manifest


def update(amv_id: str, fn: Any) -> dict:
    """Прочитать, поправить (fn(manifest)), записать — под замком (задания пишут из своих потоков)."""
    with _LOCK:
        manifest = load(amv_id)
        fn(manifest)
        return save(manifest)


def list_works(project_id: int | None = None) -> list[dict]:
    folder = root()
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("amv*/amv.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if project_id is None or int(m.get("project_id") or 0) == int(project_id):
            out.append({k: m.get(k) for k in ("id", "project_id", "title", "track_id", "aspect", "thread_id")})
    return out


def _sources(store: Any, project_id: int, source_ids: list[int]) -> list[dict]:
    from app import projects as P

    project = P.get_project(store, int(project_id), include_sources=True)
    own = {int(s["id"]): s for s in project.get("sources") or []}
    wanted = [int(s) for s in dict.fromkeys(source_ids or [])]
    if not wanted:
        raise ValueError("выбери исходники — видео проекта (фильм, серии, официальные трейлеры одного тайтла)")
    foreign = [s for s in wanted if s not in own]
    if foreign:
        raise ValueError(f"видео {', '.join(f'#{s}' for s in foreign)} не из этого проекта")
    return [own[s] for s in wanted]


def create(store: Any, project_id: int, *, title: str, source_ids: list[int], track_id: int, notes: str = "",
           aspect: str = "9:16") -> dict:
    """Новая работа AMV (без чата) — карточка и инструменты агента зовут её одинаково."""
    title = " ".join(str(title or "").split())[:100]
    if not title:
        raise ValueError("нужно название AMV")
    if aspect not in ASPECTS:
        raise ValueError(f"формат — один из {', '.join(ASPECTS)}")
    sources = _sources(store, project_id, source_ids)
    track = store.get_audio_track(int(track_id))  # нет трека — KeyError
    manifest = {
        "id": "amv" + uuid4().hex[:8], "project_id": int(project_id), "title": title,
        "source_ids": [int(s["id"]) for s in sources], "track_id": int(track["id"]),
        "track_label": str(track.get("label") or track.get("original_filename") or f"трек {track['id']}"),
        "notes": " ".join(str(notes or "").split())[:MAX_NOTES], "aspect": aspect, "thread_id": None,
        "feed_work_id": None, "created_at": round(time.time(), 1), "jobs": {}, "plans": {}, "renders": {},
    }
    return save(manifest)


# --------------------------------------------------------------------------- #
# Старт работы из карточки
# --------------------------------------------------------------------------- #


def _source_line(store: Any, source: dict) -> str:
    from app import nav

    title = nav.titles_for(store, [source]).get(int(source["id"]), "") or source.get("original_filename") or ""
    minutes = float(source.get("duration_sec") or 0) / 60
    return f"#{source['id']} «{title}»" + (f", {minutes:.0f} мин" if minutes >= 1 else "")


def first_task(manifest: dict, sources: list[str]) -> str:
    aid = manifest["id"]
    lines = [
        f"AMV «{manifest['title']}» — плейбук amv (он уже в контексте, следуй ему по шагам). Работа {aid}, формат "
        f"{manifest['aspect']}.",
        "Исходники: " + "; ".join(sources) + ". Один тайтл — одна история; несвязанные тайтлы вперемешку владелец "
        "забраковал («нихуя не понятно»). Только официальные источники.",
        f"Песня: трек #{manifest['track_id']} «{manifest['track_label']}».",
    ]
    if manifest.get("notes"):
        lines.append(f"Сюжет и пожелания владельца: {manifest['notes']}")
    lines += [
        "",
        "Первый ход:",
        f"1. amv_analyze_song(amv_id: \"{aid}\") — структура песни: части (вступление, куплет, припев, бридж, "
        "кульминация) с секундами, дропы, тихие места перед дропами. Пришли её словами.",
        "2. Исходники: source_contact_sheet по каждому — отметь acts (акты истории по секундам исходника) и exclude "
        f"(логотипы, титры, плашки с текстом, чужие вставки); потом amv_index_sources(amv_id: \"{aid}\", sources) — "
        "сцены, цвет, движение, свои вспышки исходника, дубли.",
        "3. Сюжетная арка по актам: story [{t0, t1, acts, mode, colour, note}] в секундах песни — какой акт под "
        "какую часть, где тихо (mode story: длинные кадры, наплывы), где экшен, кульминация одним фильмом. Пришли "
        "план владельцу коротко.",
        f"4. amv_plan(amv_id: \"{aid}\", test: true, story) — смотри checks (цвет, темп, родная скорость); "
        f"amv_render(amv_id: \"{aid}\", name: \"test\") — 20-секундная проба; лист кадров — amv_status(amv_id, "
        "sheet: \"test\"). Пришли пробу. Полный рендер — только после «ок» владельца.",
    ]
    return "\n".join(lines)


def start(store: Any, project_id: int, *, title: str, source_ids: list[int], track_id: int | None = None,
          music_url: str = "", notes: str = "", aspect: str = "9:16", run_agent: bool = True) -> dict:
    """Запустить AMV: исходники и песня проверены, работа заведена, переписка с плейбуком и первым заданием.
    Ответ: {amv_id, thread_id, to, track_id, track, started, task}."""
    from app import project_chat as C
    from app.project_chat_threads import set_thread_playbook

    if not " ".join(str(title or "").split()):
        raise ValueError("нужно название AMV")
    sources = _sources(store, project_id, source_ids)
    if track_id in (None, "", 0) and str(music_url or "").strip():
        from app.music_import import import_music_from_url

        track_id = int(import_music_from_url(store, str(music_url).strip(), "")["id"])
    if track_id in (None, "", 0):
        raise ValueError("выбери песню из музыки, загрузи файл или дай ссылку")
    if len(str(notes or "")) > MAX_NOTES:
        raise ValueError(f"сюжет длиннее {MAX_NOTES} знаков — коротко, по актам")
    manifest = create(store, project_id, title=title, source_ids=[int(s["id"]) for s in sources],
                      track_id=int(track_id), notes=notes, aspect=aspect)
    task = first_task(manifest, [_source_line(store, s) for s in sources])
    thread = C.create_thread(store, int(project_id), title=f"AMV: {manifest['title']}")
    set_thread_playbook(store, int(thread["id"]), PLAYBOOK)
    manifest = update(manifest["id"], lambda m: m.update(thread_id=int(thread["id"])))
    started = False
    if run_agent:
        C.send_to_thread(store, int(thread["id"]), task, origin="ui", actor="amv",
                         attachments=[{"kind": "source", "id": int(s["id"])} for s in sources])
        started = True
    return {"amv_id": manifest["id"], "thread_id": int(thread["id"]), "to": f"/chat/{thread['id']}",
            "track_id": manifest["track_id"], "track": manifest["track_label"], "aspect": manifest["aspect"],
            "started": started, "task": task}


__all__ = ["ASPECTS", "PLAYBOOK", "TEST_SIZES", "create", "first_task", "list_works", "load", "save", "start", "update",
           "work_dir"]
