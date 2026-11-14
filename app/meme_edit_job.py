"""Мем-эдит фоном: план → клипы-блоки → рендер по одному → склейка с музыкой → готовый ролик клипом.

``start(store, **options)`` → ``{job_id, status}``; ``status(job_id)`` → стадия, прогресс 0..1, блоки, итог. Задание
пишет состояние в ``<runtime>/meme_edit/jobs/<id>.json`` на каждом шаге: статус переживает перезапуск сервиса
(незаконченное задание тогда — ``interrupted``). Задания идут по одному (памяти мало: рендер и склейка тяжёлые),
следующее ждёт в очереди. Итог — обычный клип с файлом (как загруженный готовый ролик, app/clip_upload.py):
его видно в клипах, его можно скачать; клипы-блоки скрыты и остаются для правок (пересобрать — новым заданием).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

_log = logging.getLogger(__name__)

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_RUN = threading.Lock()  # одно задание за раз
SYNC = False  # тесты: задание целиком внутри start (без потока)
STAGES = ("queued", "planning", "blocks", "rendering", "concat", "done")


def _dir() -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "meme_edit" / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _save(job: dict) -> None:
    try:
        (_dir() / f"{job['job_id']}.json").write_text(json.dumps(job, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError as exc:
        _log.warning("meme edit job %s: state not saved: %s", job.get("job_id"), exc)


def _set(job_id: str, **fields: Any) -> dict:
    with _LOCK:
        job = _JOBS[job_id]
        job.update(fields)
        job["updated_at"] = round(time.time(), 1)
        snap = json.loads(json.dumps(job, ensure_ascii=False, default=str))
    _save(snap)
    return snap


def status(job_id: str) -> dict:
    """Статус задания (из памяти или с диска)."""
    with _LOCK:
        job = _JOBS.get(str(job_id))
        if job is not None:
            return json.loads(json.dumps(job, ensure_ascii=False, default=str))
    file = _dir() / f"{str(job_id)}.json"
    if not str(job_id).replace("-", "").isalnum() or not file.is_file():
        raise KeyError(f"задание мем-эдита не найдено: {job_id}")
    data = json.loads(file.read_text(encoding="utf-8"))
    if data.get("status") in ("queued", "running"):
        data["status"] = "interrupted"
        data["error"] = "сервис перезапускался, задание прервано — запустите заново (готовые блоки и кэш остаются)"
    return data


def list_jobs(limit: int = 10) -> list[dict]:
    files = sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        try:
            data = status(f.stem)
        except (KeyError, ValueError, OSError):
            continue
        out.append({k: data.get(k) for k in ("job_id", "status", "stage", "progress", "created_at", "error")})
    return out


def start(store: Any, **options: Any) -> dict:
    """Поставить мем-эдит в фон. ``options`` — как у ``run`` (source_ids/project_id, музыка, длина, форматы…)."""
    from app.meme_edit import resolve_sources

    resolve_sources(store, options.get("source_ids"), options.get("project_id"))  # ошибка входа — сразу
    job_id = "me" + uuid4().hex[:10]
    job = {"job_id": job_id, "status": "queued", "stage": "queued", "progress": 0.0, "message": "в очереди",
           "options": {k: v for k, v in options.items()}, "created_at": round(time.time(), 1), "error": "",
           "notes": [], "blocks": {}, "result": {}}
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)
    if SYNC:
        _run(store, job_id)
        return {"job_id": job_id, "status": status(job_id)["status"]}
    threading.Thread(target=_run, args=(store, job_id), name=f"meme-edit-{job_id}", daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


def _run(store: Any, job_id: str) -> None:
    with _RUN:
        try:
            _set(job_id, status="running", stage="planning", message="план")
            run(store, job_id)
        except Exception as exc:  # noqa: BLE001 - любой сбой — статус задания
            _log.exception("meme edit job %s failed", job_id)
            text = str(exc) if isinstance(exc, (KeyError, ValueError)) else f"{type(exc).__name__}: {exc}"
            _set(job_id, status="failed", error=text[:800])


def run(store: Any, job_id: str) -> dict:
    """Тело задания (в потоке; тесты зовут прямо)."""
    from app import meme_edit as ME

    job = _JOBS[job_id]
    opts = dict(job["options"])
    dry = bool(opts.pop("dry_run", False))
    do_render = opts.pop("render", True) is not False
    formats = opts.pop("formats", None) or ["horizontal", "vertical"]
    edit = ME.plan_edit(store, horizontal="horizontal" in formats, vertical="vertical" in formats,
                        say=lambda s: _set(job_id, message=s), **opts)
    _set(job_id, plan=ME.summary(edit), notes=list(edit["notes"]), progress=0.1)
    if dry:
        return _set(job_id, status="done", stage="done", progress=1.0, message="план готов (dry_run: клипов нет)")
    for sid, mic in edit["mic"].items():
        note = ME.ensure_source_audio(store, store.get_source(int(sid)), mic)
        if note:
            edit["notes"].append(note)
    base = ME._base_render(store)
    blocks_state: dict[str, list[dict]] = {}
    count_all = sum(len(f["plan"]["blocks"]) for f in edit["formats"].values())
    made = 0
    _set(job_id, stage="blocks", message="клипы-блоки")
    for name, fmt in edit["formats"].items():
        items = []
        plan = fmt["plan"]
        for i, block in enumerate(plan["blocks"]):
            clip_id = ME.build_block_clip(store, block, fmt["aspect"], mic=edit["mic"].get(int(block["source_id"])),
                                          library=edit["library"], parts=fmt["parts"], index=i,
                                          count=len(plan["blocks"]), base=base)
            items.append({"key": block["key"], "clip_id": clip_id, "status": "built"})
            made += 1
            blocks_state[name] = items
            _set(job_id, blocks=blocks_state, progress=round(0.1 + 0.1 * made / max(1, count_all), 3))
    if not do_render:
        return _set(job_id, status="done", stage="done", progress=1.0,
                    message="клипы-блоки собраны без рендера (render: false)", notes=edit["notes"])
    result: dict[str, Any] = {}
    rendered = 0
    for name, fmt in edit["formats"].items():
        files = []
        for item in blocks_state.get(name, []):
            _set(job_id, stage="rendering", message=f"рендер {name}: блок {len(files) + 1}/{len(blocks_state[name])}")
            path = _render_block(store, item["clip_id"])
            item.update(status="rendered", file=str(path))
            files.append(path)
            rendered += 1
            _set(job_id, blocks=blocks_state, progress=round(0.2 + 0.65 * rendered / max(1, count_all), 3))
        _set(job_id, stage="concat", message=f"склейка {name}")
        result[name] = _concat(store, edit, name, fmt, files, job_id)
        _set(job_id, result=result)
    return _set(job_id, status="done", stage="done", progress=1.0, message="готово", result=result,
                notes=edit["notes"])


def _render_block(store: Any, clip_id: int) -> Path:
    from app import render_jobs as RJ

    job = RJ.start(store, clip_id, requested_by="meme_edit")
    while True:
        st = RJ.wait(job["render_id"], 60.0)
        if st is None or st["status"] not in ("queued", "rendering"):
            break
    if not st or st["status"] != "done":
        raise ValueError(f"рендер блока (клип {clip_id}) не удался: {(st or {}).get('error') or 'нет статуса'}")
    result = st.get("result") or {}
    clip = store.get_clip(int(result["render_clip_id"])) if result.get("render_clip_id") else {}
    path = Path(str(clip.get("output_path") or ""))
    if not path.is_file():
        raise ValueError(f"у блока (клип {clip_id}) нет файла рендера")
    return path


def _concat(store: Any, edit: dict, name: str, fmt: dict, files: list[Path], job_id: str) -> dict:
    from app.clip_upload import register_file_clip
    from app.meme_edit_concat import concat_reel
    from app.settings import settings

    work = settings.tmp_dir / f"memeedit-{job_id}-{name}"
    out = work / f"meme_edit_{name}_{fmt['aspect'].replace(':', 'x')}.mp4"
    music = edit.get("music")
    timeline = concat_reel(fmt["plan"]["blocks"], files, out, music_path=(music or {}).get("path"),
                           segments=fmt["plan"]["music"], work=work,
                           progress=lambda s: _set(job_id, message=f"склейка {name}: {s}"))
    label = "горизонталь" if name == "horizontal" else "вертикаль «лучшее»"
    memes = sum(len(b["inserts"]) for b in fmt["plan"]["blocks"])
    description = (f"Мем-эдит: {len(fmt['plan']['blocks'])} боёв из {len(edit['sources'])} записей, {memes} мемов, "
                   f"{timeline['total']:.0f} с" + (f", музыка «{music.get('label')}»" if music else ""))
    clip = register_file_clip(store, out, f"Мем-эдит · {label}", description)
    try:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    except OSError:
        pass
    return {"clip_id": int(clip["clip_id"]), "clip_plan_id": int(clip["clip_plan_id"]), "file": clip["path"],
            "video_url": f"/media/clips/{int(clip['clip_id'])}", "duration": timeline["total"],
            "aspect": fmt["aspect"], "timeline": timeline}


def reset() -> None:
    """Для тестов."""
    with _LOCK:
        _JOBS.clear()
