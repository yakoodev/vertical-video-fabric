"""«Клип на песню»: шаги задания и фоновый работник (данные и деньги — app/music_video.py).

Каждый шаг пишет итог в папку задания (``<runtime>/music_video/mv<id>``, OUT инструмента tools/music_video) и только
потом статус: перезапуск сервиса посреди шага просто повторяет его (рендер продолжает с первого ненарисованного
кадра). Тяжёлое — в очереди тяжёлых задач (``heavy_slot``), задания идут по одному:

* разбор — app/lyrics_audio.align_audio (участок, вокал demucs при ``DEMUCS_CMD``, слова Whisper, текст владельца
  по слогам; одобренный порядок «время — атака слога, Whisper — только какое слово») + сетка долей
  tools/music_video/audio.beat_grid → ``analysis.json`` в формате CLI;
* план — черновик tools/music_video/plan.draft + промпты фонов по строкам песни (правит агент: music_video_plan);
* картинки — карточка владельцу через app/aivideo_gen (оплата там же, под её охраной);
* текстуры и сцены (``make.py --stage prep``), кадры Blender (``--stage build``), mp4 и проверки
  (``--stage encode`` и ``check``) — подпроцессами инструмента; готовый mp4 — клип проекта (app/clip_upload.py).

Blender — внешняя программа: ``MV_BLENDER`` (путь), ``blender`` в PATH или ``D:/vvf-offload/tools/blender`` (Windows).
В образе сервиса его нет — задание останавливается на ``needs_blender`` со всем, что до рендера, готовым;
``music_video_continue`` повторяет рендер, когда Blender появится. ``MUSIC_VIDEO_WORKER=0`` — без фона.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app import music_video as M

_log = logging.getLogger(__name__)

PREP_TIMEOUT_SEC = 1800
BUILD_TIMEOUT_SEC = 6 * 3600
ENCODE_TIMEOUT_SEC = 3600
BAD_JOB = ("failed", "blocked_policy", "uncertain", "cancelled")
ACTIVE_JOB = ("approved", "submitting", "polling", "recovering", "awaiting")
MAX_REPLANS = 3
_PUNCT = ",.!?…«»\"—-:;()"

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_PROCS: dict[int, subprocess.Popen] = {}  # задание → идущий подпроцесс (отмена гасит только его, по PID)


def _lock(job_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(job_id), threading.Lock())


def _say(store: Any, job_id: int, message: str) -> None:
    M.update_job(store, job_id, message=str(message)[:300])


# --------------------------------------------------------------------------- #
# Внешние программы
# --------------------------------------------------------------------------- #


def find_blender() -> str | None:
    candidates = [os.getenv("MV_BLENDER", "").strip(), shutil.which("blender")]
    if os.name == "nt":
        root = Path(os.getenv("MV_BLENDER_ROOT", "D:/vvf-offload/tools/blender"))
        candidates += [str(p) for p in sorted(root.glob("blender-*/blender.exe"), reverse=True)]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def min_ram_gb() -> float:
    try:
        return float(os.getenv("MV_MIN_RAM_GB", "6") or 6)
    except ValueError:
        return 6.0


def _ffmpeg(args: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {(proc.stderr or '').strip()[-300:]}")


def _make(store: Any, job: dict, stages: str, extra: list[str], log_name: str, timeout: int,
          progress: Any = None) -> None:
    """``tools/music_video/make.py --out OUT --stage …`` подпроцессом (своя память, свой лог в work/)."""
    out = M.job_dir(job)
    cmd = [sys.executable, str(M.tools_dir() / "make.py"), "--out", str(out), "--stage", stages,
           "--name", f"mv{job['id']}", "--style", job["style"], *extra]
    if (job.get("options") or {}).get("draft"):
        cmd.append("--draft")
    log = out / "work" / log_name
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    started = time.monotonic()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(M.tools_dir()), env=env)
        _PROCS[int(job["id"])] = proc
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    proc.kill()
                    raise RuntimeError(f"{stages}: дольше {timeout // 60} мин — остановлено (лог {log.name})")
                if progress is not None:
                    try:
                        progress()
                    except Exception:  # noqa: BLE001 - прогресс — подсказка
                        pass
                time.sleep(3)
        finally:
            _PROCS.pop(int(job["id"]), None)
    if M.get_job(store, int(job["id"]))["status"] == "cancelled":
        raise _Cancelled()
    if proc.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise RuntimeError(f"{stages} упал ({proc.returncode}): " + " | ".join(tail)[-500:])


class _Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# 1. Разбор песни
# --------------------------------------------------------------------------- #


def _clean(word: Any) -> str:
    return str(word or "").strip().strip(_PUNCT).strip()


def words_from_lines(lines: list[dict], start: float, end: float) -> list[dict]:
    """Строки выравнивания (app/lyrics_align) → слова формата analysis.json CLI: w, s, e, p, on (атака), syl, snap."""
    out = []
    for line in lines:
        for word in line.get("words") or []:
            text = _clean(word.get("w"))
            on = float(word.get("t0") or 0.0)
            if not text or not start - 0.05 <= on < end:
                continue
            out.append({"w": text, "s": round(on, 3), "e": round(float(word.get("t1") or on), 3),
                        "p": 1.0 if word.get("how") == "heard" else 0.5, "on": round(on, 3),
                        "syl": [round(float(t), 3) for t in (word.get("syl") or [on])],
                        "snap": word.get("snap"), "line": int(line.get("i") or 0),
                        **({"unsure": True} if line.get("unsure") else {})})
    out.sort(key=lambda w: w["on"])
    return out


def _duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, timeout=60)
    return float((proc.stdout or "0").strip() or 0)


def analyze(store: Any, job: dict) -> dict:
    from app import frag_movie as F
    from app import lyrics_align as LA
    from app import lyrics_audio as LAU

    out = M.job_dir(job)
    work = out / "work"
    track = store.get_audio_track(int(job["track_id"]))
    song = Path(str(track.get("file_path") or ""))
    if not song.is_file():
        raise M.MvError(f"у трека #{track['id']} нет файла")
    start = float(job["start_sec"] or 0.0)
    end = float(job["end_sec"]) if job.get("end_sec") is not None else _duration(song)
    saved = F.track_lyrics(store, int(track["id"]))
    lyrics, lang = saved.get("text", ""), str(job.get("lang") or saved.get("lang") or "")
    lyr = work / "lyrics"
    shutil.rmtree(lyr, ignore_errors=True)
    result = LAU.align_audio(song, lyrics=lyrics, lang=lang, start=start, end=end, keep_dir=lyr,
                             progress=lambda s: _say(store, int(job["id"]), f"разбор песни: {s}"))
    _say(store, int(job["id"]), "разбор песни: сетка долей")
    _ffmpeg(["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(song), "-vn", "-ac", "2", "-ar", "48000",
             "-c:a", "pcm_s16le", str(work / "audio.wav")])
    stem = work / "sep" / "htdemucs" / "mix44" / "vocals.wav"  # sync_check CLI берёт вокал отсюда
    stem.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lyr / "vocals.wav", stem)
    audio = M.tool_module("audio")
    mix, msr = audio.load_wav(str(lyr / "mix.wav"))
    grid = audio.beat_grid(mix, msr, 0.0, end - start)
    grid["beats"] = [round(b + start, 3) for b in grid["beats"]]
    grid["downbeats"] = [round(b + start, 3) for b in grid["downbeats"]]
    x, sr = LAU.read_mono(lyr / "vocals.wav")
    onsets = LA.vocal_envelope(x, sr, t0=start)["onsets"]
    words = words_from_lines(result["lines"], start, end)
    an = {"song": str(song), "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3),
          **grid, "onsets": onsets, "words": words, "method": result.get("method"), "lang": result.get("lang"),
          "with_text": result.get("with_text"), "stats": result.get("stats"), "align_notes": result.get("notes")}
    text = json.dumps(an, ensure_ascii=False, indent=1)
    (work / "analysis.json").write_text(text, encoding="utf-8")
    (out / "analysis.json").write_text(text, encoding="utf-8")
    shutil.rmtree(lyr / "sep", ignore_errors=True)  # стем demucs уже скопирован — гигабайты не держим
    notes = list(result.get("notes") or [])
    if not words:
        notes.append("слов в участке не нашлось (инструментал?) — клип без больших слов и подписей")
    M.update_job(store, int(job["id"]), notes=[*job.get("notes", []), *notes][-12:])
    return an


def load_analysis(job: dict) -> dict | None:
    path = M.job_dir(job) / "analysis.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 2. План
# --------------------------------------------------------------------------- #


def make_plan(store: Any, job: dict) -> str:
    an = load_analysis(job)
    if an is None:
        raise M.MvError("нет разбора песни (analysis.json)")
    plan = M.load_plan(job)
    if plan is None:
        plan = M.save_plan(job, M.draft_plan(job, an))
    opts = job.get("options") or {}
    if opts.get("review_plan") and not opts.get("plan_reviewed"):
        M.set_status(store, int(job["id"]), "plan_review", ("analyzed",),
                     message="черновик плана готов: правь music_video_plan (концепция, персонажи, картинка на строку), "
                             "потом music_video_continue")
        return "plan_review"
    M.set_status(store, int(job["id"]), "planned", ("analyzed",), message="план готов — считаю картинки")
    return "planned"


def invalidate_render(job: dict) -> None:
    """План или картинки поменялись: текстуры, сцены и кадры — заново."""
    out = M.job_dir(job)
    work = out / "work"
    for name in ("tex", "frames", "stills"):
        shutil.rmtree(work / name, ignore_errors=True)
    for path in (work / "scene.json", out / "bundle.zip", out / f"mv{job['id']}.mp4"):
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 3. Картинки: оценка → карточка → ожидание
# --------------------------------------------------------------------------- #


def estimate(store: Any, job: dict) -> dict:
    from app import aivideo_gen as G

    return G.estimate(store, int(job["project_id"]), M.STAGE, None, [M.group_of(int(job["id"]))])


def request_images(store: Any, job: dict) -> str:
    from app import aivideo as V
    from app import aivideo_gen as G

    jid, pid = int(job["id"]), int(job["project_id"])
    opts = dict(job.get("options") or {})
    regen = [str(x) for x in opts.pop("regenerate", None) or []]
    plan = M.load_plan(job) or {}
    if regen:
        items = [f"{M.TARGET}:{jid}:{M.image_kind(plan, i)}:{i}" for i in regen if i in plan.get("images", {})]
        M.update_job(store, jid, options=opts)
    else:
        items = [M.group_of(jid)]
    est = G.estimate(store, pid, M.STAGE, None, items, regenerate=bool(regen))
    if not est["items"]:
        nxt = "generating" if est["busy"] else "images_ready"
        M.set_status(store, jid, nxt, ("planned",), estimate_rub=0.0,
                     message="картинки уже заказаны — жду" if est["busy"] else "картинки есть — 0 ₽")
        return nxt
    if est["paid"] and est["total_rub"] > float(job["max_rub"] or 0) + 1e-6:
        error = (f"смета картинок {est['total_rub']:.2f} ₽ ({est['count']} шт.) больше потолка задания "
                 f"{float(job['max_rub'] or 0):g} ₽ — карточку не отправляю: меньше картинок в плане (images), свои "
                 "картинки (asset_id) или выше max_rub (music_video_continue после правки)")
        M.set_status(store, jid, "failed", ("planned",), error=error, estimate_rub=est["total_rub"])
        return "failed"
    note = f"клип на песню «{job['title']}»"
    try:
        req = G.request_stage(store, pid, M.STAGE, None, items, regenerate=bool(regen), requested_by="music_video",
                              note=note)
    except (G.BudgetError, V.AivError, M.MvError) as exc:
        M.set_status(store, jid, "failed", ("planned",), error=str(exc)[:600], estimate_rub=est["total_rub"])
        return "failed"
    status = req.get("status")
    if status == "awaiting_approval":
        M.set_status(store, jid, "awaiting_approval", ("planned",), approval_id=req["approval_id"],
                     estimate_rub=est["total_rub"], message=req.get("note") or "карточка ушла владельцу")
        return "awaiting_approval"
    M.set_status(store, jid, "generating", ("planned",), approval_id=req.get("approval_id"),
                 estimate_rub=est["total_rub"] if est["paid"] else 0.0,
                 message=req.get("note") or "картинки рисуются")
    return "generating"


def _last_image_job(store: Any, job: dict, kind: str, iid: str) -> dict | None:
    row = store.db.query_one(
        "SELECT * FROM aiv_gen_jobs WHERE project_id = ? AND target_type = ? AND target_id = ? AND ref_kind = ? "
        "AND slot = ? ORDER BY id DESC LIMIT 1", (int(job["project_id"]), M.TARGET, int(job["id"]), kind, iid))
    return dict(row) if row else None


def readiness(store: Any, job: dict) -> dict:
    """Все ли картинки плана готовы (своя, или версия с тем же промптом), что идёт, что упало, что держится."""
    plan = M.load_plan(job) or {}
    ready, missing, failed, active, held = [], [], [], [], []
    for iid, spec in (plan.get("images") or {}).items():
        if spec.get("asset_id"):
            ready.append(iid)
            continue
        kind = M.image_kind(plan, iid)
        ref = M.image_ref(store, job, iid, plan)
        if ref is not None and (ref.get("prompt") or "") == M.style_prompt(plan, iid):
            ready.append(iid)
            continue
        missing.append(iid)
        run = _last_image_job(store, job, kind, iid)
        if run is None:
            continue
        if run["status"] in BAD_JOB:
            failed.append(f"{iid}: {run['status']} — {(run.get('error') or '')[:160]}")
        elif run["status"] == "held":
            why = run.get("error") or "держится"
            held.append(f"{iid}: " + ("платная генерация выключена (AIVIDEO_ENABLED=0)" if "AIVIDEO_ENABLED" in why
                                      else why[:160]))
        elif run["status"] in ACTIVE_JOB:
            active.append(iid)
    return {"ready": ready, "missing": missing, "failed": failed, "active": active, "held": held,
            "all_ready": not missing}


def check_images(store: Any, job: dict) -> str:
    from app import autopilot as A

    jid = int(job["id"])
    if job["status"] == "awaiting_approval" and job.get("approval_id"):
        card = A.get_approval(store, int(job["approval_id"]))
        if card["status"] == "pending":
            return "wait"
        if card["status"] != "approved":
            M.set_status(store, jid, "cancelled", ("awaiting_approval",),
                         error=f"карточка #{card['id']}: {card['status']}"
                               + (f" — {card.get('decision_comment')}" if card.get("decision_comment") else ""))
            return "cancelled"
        M.set_status(store, jid, "generating", ("awaiting_approval",), message="карточка одобрена — рисуются картинки")
    state = readiness(store, job)
    if state["all_ready"]:
        M.set_status(store, jid, "images_ready", ("generating", "awaiting_approval"),
                     message=f"картинок готово: {len(state['ready'])}")
        return "images_ready"
    if state["active"]:
        _say(store, jid, f"рисуются картинки: готово {len(state['ready'])}, идут {len(state['active'])}")
        return "wait"
    if state["held"] and not state["failed"]:
        _say(store, jid, "ждёт запуска: " + "; ".join(state["held"][:2]))
        return "wait"
    if state["failed"]:
        M.set_status(store, jid, "failed", ("generating", "awaiting_approval"),
                     error="картинки не получились: " + "; ".join(state["failed"])[:600])
        return "failed"
    opts = dict(job.get("options") or {})
    opts["replans"] = int(opts.get("replans") or 0) + 1
    M.update_job(store, jid, options=opts)
    if opts["replans"] > MAX_REPLANS:
        M.set_status(store, jid, "failed", ("generating", "awaiting_approval"),
                     error="картинок нет, а запусков по ним нет — проверь план (music_video_status)")
        return "failed"
    M.set_status(store, jid, "planned", ("generating", "awaiting_approval"), message="картинок не хватает — заказываю")
    return "planned"


# --------------------------------------------------------------------------- #
# 4. Текстуры и сцены, кадры, mp4
# --------------------------------------------------------------------------- #


def collect_images(store: Any, job: dict) -> list[str]:
    from app.aivideo_prompts import _asset_path

    plan = M.load_plan(job) or {}
    img = M.job_dir(job) / "work" / "img"
    shutil.rmtree(img, ignore_errors=True)
    img.mkdir(parents=True, exist_ok=True)
    got, missing = [], []
    for iid, spec in (plan.get("images") or {}).items():
        path = (M.own_asset_path(store, job, spec) if spec.get("asset_id")
                else _asset_path(store, M.image_ref(store, job, iid, plan)))
        if path is None:
            missing.append(iid)
            continue
        shutil.copyfile(path, img / f"{iid}.jpg")  # cv2.imread узнаёт формат по содержимому
        got.append(iid)
    if missing:
        raise M.MvError("нет файлов картинок: " + ", ".join(missing))
    return got


def prepare(store: Any, job: dict) -> None:
    from app.heavy_queue import heavy_slot

    invalidate_render(job)
    collect_images(store, job)
    _say(store, int(job["id"]), "текстуры, буквы и сцены")
    with heavy_slot("music_video", f"клип на песню {job['id']}: сцены"):
        _make(store, job, "prep", [], "prep.log", PREP_TIMEOUT_SEC)
    if not (M.job_dir(job) / "work" / "scene.json").is_file():
        raise M.MvError("prep не записал scene.json (см. work/prep.log)")
    M.set_status(store, int(job["id"]), "prepared", ("images_ready",), message="сцены готовы — рендер кадров")


def render_state(job: dict) -> dict:
    """Кадров всего (scene.json) и нарисовано подряд с нулевого."""
    work = M.job_dir(job) / "work"
    try:
        nf = int(json.loads((work / "scene.json").read_text(encoding="utf-8"))["nf"])
    except (OSError, ValueError, KeyError):
        nf = 0
    frames = work / "frames"
    done = 0
    while (frames / f"f_{done:04d}.png").is_file():
        done += 1
    return {"nf": nf, "done": min(done, nf) if nf else done}


def remote_enabled() -> bool:
    return os.getenv("MV_REMOTE_RENDER", "1").strip().lower() not in ("0", "false", "no", "off")


BUNDLE_FILES = ("plan.json", "analysis.json", "work/audio.wav")


def bundle_path(store: Any, job: dict) -> Path:
    """Всё, что нужно хост-воркеру для кадров и mp4: план, разбор, картинки, звук участка (текстуры он делает сам —
    они в разы больше картинок). Собирается один раз на версию сцен."""
    import zipfile

    out = M.job_dir(job)
    work = out / "work"
    dest = out / "bundle.zip"
    scene = work / "scene.json"
    if dest.is_file() and scene.is_file() and dest.stat().st_mtime >= scene.stat().st_mtime:
        return dest
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for name in BUNDLE_FILES:
            if not (out / name).is_file():
                raise M.MvError(f"нет {name} — сначала разбор и сцены")
            zf.write(out / name, name)
        for img in sorted((work / "img").glob("*.jpg")):
            zf.write(img, f"work/img/{img.name}")
    tmp.replace(dest)
    return dest


def _remote(store: Any, job: dict) -> dict | None:
    from app import render_queue as RQ

    rid = (job.get("options") or {}).get("remote_job")
    if not rid:
        return None
    try:
        return RQ.get(store, int(rid))
    except KeyError:
        return None


def render_remote(store: Any, job: dict) -> str:
    """Нет Blender здесь: задание хост-воркеру (kind ``music_video``), ждать его mp4."""
    import hashlib

    from app import render_queue as RQ

    jid = int(job["id"])
    bundle = bundle_path(store, job)
    stamp = f"{bundle.stat().st_size}:{bundle.stat().st_mtime_ns}".encode()
    key = f"mv{jid}:" + hashlib.sha1(stamp).hexdigest()[:12]  # новая версия сцен — новое задание компьютеру
    remote = _remote(store, job)
    if (remote is None or remote.get("key") != key
            or (remote["status"] in ("failed", "cancelled") and job["status"] != "remote_render")):
        state = render_state(job)
        remote = RQ.enqueue(store, {"mv_job": jid, "bundle": f"/api/music-video/{jid}/bundle", "name": f"mv{jid}",
                                    "style": job["style"], "nf": state["nf"],
                                    "draft": bool((job.get("options") or {}).get("draft"))},
                            key=key, kind="music_video", title=f"Клип на песню «{job['title']}»")
        M.update_job(store, jid, options={**(job.get("options") or {}), "remote_job": int(remote["id"])})
        if job["status"] != "remote_render":
            M.set_status(store, jid, "remote_render", ("prepared", "needs_blender"))
    if remote["status"] in ("queued", "running"):
        label, note = RQ.wait_label(store, remote)
        if remote["status"] == "queued" and not RQ.worker_online(store):
            note = ("Blender в образе сервиса нет — кадры рисует компьютер с Blender. Запусти на нём: "
                    "python tools/render_worker.py --server <адрес сервиса> (токен как у MCP)")
        _say(store, jid, f"{label}" + (f" — {note}" if note else ""))
        return "wait"
    if remote["status"] == "done":
        result = RQ.result_file(remote)
        if result is None:
            raise M.MvError(f"рендер #{remote['id']} готов, а файла нет")
        shutil.copyfile(result, M.job_dir(job) / f"mv{jid}.mp4")
        M.update_job(store, jid, options={**(M.get_job(store, jid).get("options") or {}), "remote_mp4": True})
        M.set_status(store, jid, "rendered", ("remote_render",), message="кадры и mp4 с компьютера — проверки")
        return "rendered"
    M.set_status(store, jid, "failed", ("remote_render",),
                 error=f"рендер на компьютере: {remote['status']} — {(remote.get('error') or '')[:400]}")
    return "failed"


def render(store: Any, job: dict) -> str:
    from app.heavy_queue import heavy_slot

    jid = int(job["id"])
    exe = find_blender()
    if not exe:
        if remote_enabled():
            return render_remote(store, job)
        text = ("Blender здесь не установлен (в образе сервиса его нет), очередь компьютера выключена "
                "(MV_REMOTE_RENDER=0): кадры не рисую. Всё до рендера готово — картинки, текстуры, scene.json; "
                "рендер — где есть Blender (MV_BLENDER), потом music_video_continue")
        if job["status"] != "needs_blender":
            M.set_status(store, jid, "needs_blender", ("prepared",), message=text)
        elif job.get("message") != text:
            _say(store, jid, text)
        return "wait"
    if job["status"] == "needs_blender":
        M.set_status(store, jid, "prepared", ("needs_blender",), message="Blender найден — рендер кадров")
    state = render_state(job)
    if not state["nf"]:
        raise M.MvError("нет scene.json — сначала сцены (prep)")
    if state["done"] < state["nf"]:
        extra = ["--blender", exe, "--min-ram", f"{min_ram_gb():g}"]
        if state["done"]:
            extra += ["--frames", f"{state['done']}-{state['nf'] - 1}"]  # продолжить: make.py не стирает кадры
        _say(store, jid, f"рендер кадров: {state['done']}/{state['nf']}")

        def tick() -> None:
            now = render_state(job)
            _say(store, jid, f"рендер кадров: {now['done']}/{now['nf']}")

        with heavy_slot("music_video", f"клип на песню {jid}: кадры Blender"):
            _make(store, job, "build", extra, "build.log", BUILD_TIMEOUT_SEC, progress=tick)
        state = render_state(job)
        if state["done"] < state["nf"]:
            raise RuntimeError(f"Blender нарисовал {state['done']} из {state['nf']} кадров (work/render.log)")
    M.set_status(store, jid, "rendered", ("prepared",), message=f"кадров {state['nf']} — собираю mp4")
    return "rendered"


def finish(store: Any, job: dict) -> dict:
    from app.clip_upload import register_file_clip
    from app.heavy_queue import heavy_slot

    jid = int(job["id"])
    out = M.job_dir(job)
    notes = list(job.get("notes") or [])
    _say(store, jid, "mp4 и проверки")
    with heavy_slot("music_video", f"клип на песню {jid}: mp4"):
        if not (job.get("options") or {}).get("remote_mp4"):  # с компьютера mp4 приходит готовым
            _make(store, job, "encode", [], "encode.log", ENCODE_TIMEOUT_SEC)
        try:
            _make(store, job, "check", [], "check.log", ENCODE_TIMEOUT_SEC)
        except RuntimeError as exc:
            notes.append(f"проверка (лист кадров, sync_check) не сделана: {str(exc)[:200]}")
    video = out / f"mv{jid}.mp4"
    if not video.is_file():
        raise M.MvError("encode не записал mp4 (work/encode.log)")
    (out / f"mv{jid}_tg.mp4").unlink(missing_ok=True)  # в клипы идёт оригинал; лёгкую копию делает сервис сам
    label = (job.get("options") or {}).get("track_label") or ""
    desc = f"Клип на песню ({M.STYLE_LABELS.get(job['style'], job['style'])}): {label}".strip()
    if M.is_dry(job):
        desc += " — сухой прогон, заглушки вместо картинок"
    clip = register_file_clip(store, video, job["title"], desc, int(job["project_id"]))
    opts = {k: v for k, v in (M.get_job(store, jid).get("options") or {}).items() if k != "remote_mp4"}
    M.update_job(store, jid, notes=notes[-12:], options=opts)
    if not M.is_dry(job):
        feed = to_feed(store, M.get_job(store, jid), int(clip["clip_id"]), desc)
        opts["feed"] = feed if feed.get("work_id") else {**(opts.get("feed") or {}), "error": feed.get("error")}
        M.update_job(store, jid, options=opts)
    M.set_status(store, jid, "done", ("rendered",), clip_id=int(clip["clip_id"]),
                 clip_plan_id=int(clip["clip_plan_id"]), error="", message=f"готово: клип #{clip['clip_plan_id']}")
    return clip


def to_feed(store: Any, job: dict, clip_id: int, note: str) -> dict:
    """Готовый клип на песню — новой версией его работы в «Ленте» (как у AMV): владелец смотрит всё готовое в одном
    месте, а не в Telegram. Работа — одна на задание (повторный рендер — следующая версия). Сбой ленты клип не
    отменяет: ответ с error."""
    from app import feed as FEED

    try:
        opts = dict(job.get("options") or {})
        work_id = (opts.get("feed") or {}).get("work_id")
        if not work_id:
            work_id = FEED.create_work(store, f"Клип на песню · {job['title']}", job["project_id"], kind="other",
                                       made_by="agent")["id"]
        version = FEED.add_version(store, int(work_id), note=note, variants=[{"clip_id": int(clip_id)}],
                                   status="review", made_by="agent")  # подпись варианта — по кадру (9:16, 16:9)
        return {"work_id": int(work_id), "version_id": int(version["id"])}
    except Exception as exc:  # noqa: BLE001 - лента — витрина, клип уже есть
        return {"error": f"в ленту не добавлено: {str(exc)[:200]}"}


# --------------------------------------------------------------------------- #
# Ведущий
# --------------------------------------------------------------------------- #

STEP_LABELS = {"new": "разбор песни", "analyzed": "план", "planned": "оценка картинок",
               "awaiting_approval": "одобрение", "generating": "картинки", "images_ready": "сцены",
               "prepared": "рендер", "needs_blender": "рендер", "remote_render": "рендер на компьютере",
               "rendered": "сборка mp4"}


def advance(store: Any, job_id: int, max_steps: int = 12) -> dict:
    """Двигать задание, пока не упрётся в ожидание (карточка, картинки, Blender, правка плана) или конец."""
    lock = _lock(job_id)
    if not lock.acquire(blocking=False):
        return {**M.get_job(store, job_id), "busy": True}
    try:
        for _ in range(max_steps):
            job = M.get_job(store, job_id)
            status = job["status"]
            try:
                if status == "new":
                    analyze(store, job)
                    M.set_status(store, job_id, "analyzed", ("new",), message="песня разобрана — черновик плана")
                elif status == "analyzed":
                    if make_plan(store, job) == "plan_review":
                        break
                elif status == "planned":
                    if request_images(store, job) in ("awaiting_approval", "failed"):
                        break
                elif status in ("awaiting_approval", "generating"):
                    if check_images(store, job) in ("wait", "failed", "cancelled"):
                        break
                elif status == "images_ready":
                    prepare(store, job)
                elif status in ("prepared", "needs_blender", "remote_render"):
                    if render(store, job) in ("wait", "failed"):
                        break
                elif status == "rendered":
                    finish(store, job)
                else:
                    break
            except _Cancelled:
                break
            except Exception as exc:  # noqa: BLE001 - шаг упал: задание ждёт правки
                _log.warning("music video %s step %s failed: %s", job_id, status, exc)
                if M.get_job(store, job_id)["status"] == status:
                    M.set_status(store, job_id, "failed", (status,),
                                 error=f"{STEP_LABELS.get(status, status)}: {str(exc)[:600]}")
                break
    finally:
        lock.release()
    return M.get_job(store, job_id)


def _resume_status(job: dict) -> str:
    out = M.job_dir(job)
    if not (out / "analysis.json").is_file():
        return "new"
    if M.load_plan(job) is None:
        return "analyzed"
    if not (out / "work" / "scene.json").is_file():
        return "planned"
    state = render_state(job)
    if state["done"] < state["nf"] or not state["nf"]:
        return "prepared"
    return "rendered"


def resume(store: Any, job_id: int, regenerate: list[str] | None = None, max_rub: Any = None) -> dict:
    """Продолжить: правка плана принята (plan_review), упавшее/отменённое — с шага, где есть данные; Blender появился;
    ``regenerate`` — перерисовать эти картинки (своя карточка); ``max_rub`` — новый потолок задания."""
    job = M.get_job(store, job_id)
    opts = dict(job.get("options") or {})
    if max_rub not in (None, ""):
        value = float(max_rub)
        if value < 0:
            raise M.MvError("max_rub — неотрицательная сумма")
        store.db.execute("UPDATE mv_jobs SET max_rub = ? WHERE id = ?", (round(value, 2), int(job_id)))
    if job["status"] in ("awaiting_approval", "generating") and regenerate:
        raise M.MvError("картинки ещё рисуются или ждут одобрения — перерисовка после них")
    if regenerate:
        plan = M.load_plan(job) or {}
        unknown = [i for i in regenerate if i not in (plan.get("images") or {})]
        if unknown:
            raise M.MvError("в плане нет картинок: " + ", ".join(unknown))
        opts["regenerate"] = list(dict.fromkeys(regenerate))
    opts["plan_reviewed"] = True
    opts["replans"] = 0
    M.update_job(store, job_id, options=opts)
    status = job["status"]
    if regenerate and status not in ("new", "analyzed"):
        invalidate_render(job)
        M.set_status(store, job_id, "planned", None, error="", message="перерисовываю картинки")
    elif status == "plan_review":
        M.set_status(store, job_id, "planned", ("plan_review",), message="план принят — считаю картинки")
    elif status in ("failed", "cancelled", "done"):
        M.set_status(store, job_id, _resume_status(job), (status,), error="")
    elif status in ("needs_blender", "remote_render"):
        remote = _remote(store, job)
        if remote is None or remote["status"] not in ("queued", "running"):  # компьютер рисует — ждём его
            M.set_status(store, job_id, "prepared", (status,))
    kick()
    return M.get_job(store, job_id)


def set_plan(store: Any, job_id: int, plan: dict) -> dict:
    """Новый план (агент): картинки с изменённым промптом перерисуются по новой карточке, сцены и кадры — заново."""
    job = M.get_job(store, job_id)
    if job["status"] in ("new",):
        raise M.MvError("песня ещё разбирается — план после разбора (номера слов w:N — из analysis.json)")
    if job["status"] in ("awaiting_approval", "generating"):
        raise M.MvError("картинки ждут одобрения или рисуются — дождись их или отмени задание (music_video_continue "
                        "cancel)")
    if _lock(job_id).locked():
        raise M.MvError("задание сейчас на тяжёлом шаге (сцены/рендер/mp4) — подожди его")
    saved = M.save_plan(job, plan)
    invalidate_render(job)
    if job["status"] not in ("plan_review", "analyzed"):
        M.set_status(store, job_id, "planned", None, error="", message="план изменён — считаю картинки")
        kick()
    return saved


def cancel(store: Any, job_id: int) -> dict:
    from app import autopilot as A

    job = M.get_job(store, job_id)
    if job["status"] in M.TERMINAL:
        return job
    if job.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'клип на песню отменён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(job["approval_id"]),))
        try:
            card = A.get_approval(store, int(job["approval_id"]))
            if card["status"] == "pending":
                A._update(store, card["id"], status="expired", decision_comment="клип на песню отменён")
        except KeyError:
            pass
    remote = _remote(store, job)
    if remote is not None and remote["status"] in ("queued", "running"):
        from app import render_queue as RQ

        RQ.cancel(store, int(remote["id"]))
    M.set_status(store, job_id, "cancelled", None, error="отменено")
    proc = _PROCS.get(int(job_id))
    if proc is not None and proc.poll() is None:
        proc.kill()  # свой подпроцесс задания (Blender/ffmpeg внутри make.py), по PID
    return M.get_job(store, job_id)


def create(store: Any, project_id: int, run: bool = False, **kwargs: Any) -> dict:
    job = M.create_job(store, project_id, **kwargs)
    if run:
        return advance(store, int(job["id"]))
    kick()
    return job


# --------------------------------------------------------------------------- #
# Вид для агента и UI
# --------------------------------------------------------------------------- #


def progress_of(job: dict) -> float:
    base = M.PROGRESS.get(job["status"], 0.0)
    if job["status"] == "prepared":
        state = render_state(job)
        if state["nf"]:
            return round(base + (M.PROGRESS["rendered"] - base) * state["done"] / state["nf"], 3)
    return base


def view(store: Any, job_id: int) -> dict:
    from app import autopilot as A

    job = M.get_job(store, job_id)
    out = {k: job.get(k) for k in ("id", "project_id", "track_id", "title", "style", "style_label", "start_sec",
                                   "end_sec", "max_rub", "status", "status_label", "message", "error", "approval_id",
                                   "estimate_rub", "clip_id", "clip_plan_id", "notes", "created_at", "updated_at")}
    out["progress"] = progress_of(job)
    out["dry_run"] = M.is_dry(job)
    out["options"] = {k: v for k, v in (job.get("options") or {}).items() if k in ("images", "review_plan", "draft")}
    if job.get("approval_id"):
        try:
            card = A.get_approval(store, int(job["approval_id"]))
            out["approval"] = {"id": card["id"], "status": card["status"], "decided_by": card.get("decided_by"),
                               "amount_rub": (card.get("payload") or {}).get("amount_rub")}
        except KeyError:
            pass
    an = load_analysis(job)
    if an:
        out["analysis"] = {"bpm": an.get("bpm"), "words": len(an.get("words") or []), "method": an.get("method"),
                           "lang": an.get("lang"), "duration": an.get("duration"),
                           "notes": an.get("align_notes") or []}
    plan = M.load_plan(job)
    if plan:
        state = readiness(store, job)
        out["images"] = []
        for iid, spec in plan["images"].items():
            ref = None if spec.get("asset_id") else M.image_ref(store, job, iid, plan)
            out["images"].append({"id": iid, "kind": spec.get("kind", "plate"), "own": bool(spec.get("asset_id")),
                                  "ready": iid in state["ready"], "refs": spec.get("refs") or [],
                                  "asset_id": spec.get("asset_id") or (ref or {}).get("asset_id"),
                                  "url": (ref or {}).get("url"), "prompt": str(spec.get("prompt") or "")[:160]})
        out["scenes"] = len(plan.get("scenes") or [])
        if state["failed"] or state["held"]:
            out["image_problems"] = state["failed"] + state["held"]
    if job["status"] in ("prepared", "needs_blender", "rendered", "done"):
        out["render"] = render_state(job)
    remote = _remote(store, job)
    if remote is not None:
        out["remote_render"] = {"id": remote["id"], "status": remote["status"], "progress": remote.get("progress"),
                                "worker": remote.get("worker"), "error": remote.get("error") or ""}
        if job["status"] == "remote_render":
            base = M.PROGRESS["remote_render"]
            out["progress"] = round(base + (M.PROGRESS["rendered"] - base) * float(remote.get("progress") or 0), 3)
    folder = M.job_dir(job)
    out["files"] = {name: f"/api/music-video/{job['id']}/file/{name}" for name in FILES
                    if (folder / FILES[name]).is_file()}
    out["blender"] = bool(find_blender())
    if job.get("clip_plan_id"):
        out["clip_url"] = f"/clips/{job['clip_plan_id']}"
    feed = (job.get("options") or {}).get("feed")
    if feed:
        out["feed"] = {**feed, **({"url": f"/feed?work={feed['work_id']}"} if feed.get("work_id") else {})}
    out["next"] = NEXT.get(job["status"], "")
    return out


FILES = {"sheet.jpg": "sheet.jpg", "sync_check.mp4": "sync_check.mp4", "plan.json": "plan.json",
         "analysis.json": "analysis.json", "stills_sheet.jpg": "work/stills_sheet.jpg"}
NEXT = {
    "plan_review": "music_video_plan(job_id) — прочитай черновик, перепиши концепцию (персонажи в characters, "
                   "картинка на строку, большие слова), music_video_plan(job_id, plan), потом music_video_continue",
    "awaiting_approval": "карточка у владельца: сам не одобряй и не обходи; жди решения",
    "needs_blender": "рендер — где есть Blender; владельцу честно: всё до рендера готово, кадров нет",
    "remote_render": "кадры рисует компьютер с Blender (tools/render_worker.py); не в сети — скажи владельцу запустить",
    "failed": "прочитай error; поправь (план, потолок max_rub) и music_video_continue",
    "done": "посмотри sheet.jpg (лист кадров) и sync_check.mp4; клип (кроме сухого прогона) уже в «Ленте» на проверке (поле feed) — владельцу "
            "одна строка со ссылкой; брак — правка плана и music_video_continue (новая версия в той же работе)",
}


# --------------------------------------------------------------------------- #
# Работник
# --------------------------------------------------------------------------- #

_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class MusicVideoWorker:
    """Фон: ведёт задания по шагам по одному. Платные картинки выполняет работник ИИ-видео после «да»."""

    IDLE_SECONDS = 10

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        global _wake
        if os.getenv("MUSIC_VIDEO_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="music-video", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        marks = ", ".join("?" for _ in M.ACTIVE_STATUSES)
        rows = self.store.db.query_all(f"SELECT id FROM mv_jobs WHERE status IN ({marks}) ORDER BY id",
                                       M.ACTIVE_STATUSES)
        done = []
        for row in rows:
            if self._stop.is_set():
                break
            advance(self.store, int(row["id"]))
            done.append(int(row["id"]))
        return done

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("music video worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()


__all__ = ["MusicVideoWorker", "advance", "analyze", "cancel", "create", "estimate", "find_blender", "kick",
           "readiness", "render_state", "resume", "set_plan", "view", "words_from_lines"]
