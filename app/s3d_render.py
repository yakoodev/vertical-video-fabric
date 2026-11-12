"""Задание рендера 3D-серии: голос → слова → рот → 3D (node/vrm в Chromium) → проверки → лист кадров → холст-клип.

Статусы задания (``app/s3d_store.JOB_STATUSES``), ведёт ``advance`` (работник ``Series3dWorker``, «Рендер» в UI,
``series3d_render`` агента) — каждый шаг повторяем и идемпотентен:

1. ``queued`` → версия плана проверяется ещё раз (библиотека могла измениться), движок должен рисоваться в сервисе.
2. ``voice`` → недостающие реплики озвучиваются (``app/s3d_voice.request``): бесплатное — сразу; платный голос —
   ``awaiting_approval`` с карточкой владельцу (сумма в ₽), дальше — только после «Разрешить».
3. ``render`` → в очереди тяжёлых задач и под замком Chromium (``app/browser_lock.browser_slot``, одно за раз):
   дорожка голоса, слова (Whisper), огибающая рта, спека VRM (``app/s3d_vrm.compile_vrm``), рендер
   ``node/scene3d/render.mjs`` с бандлом ``node/vrm/dist/vrm.js``, проверки касаний ``node/vrm/contact.mjs``, лист
   кадров. Проверки (``checks``) — список {id, label, ok, detail}: пустой замер — провал, а не «всё хорошо».
   Пустые проверки или героини нет в кадре — ``failed`` (клип не собирается); нарушения касаний — клип есть, но в
   карточке серии видно, что не так.
4. ``done`` — холст-клип (``app/s3d_clip.assemble``): 3D-видео, голос, звуки, музыка, субтитры, заголовки,
   переходы — обычный клип: публикуется и правится как другие.

Рендер подменяется в тестах (``render_vrm``, ``check_vrm``). ``SERIES3D_WORKER=0`` — работник не запускается.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import s3d_store as D
from app.s3d_plan import RENDERABLE, PlanError, validate_plan
from app.settings import settings

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
VRM_DIR = ROOT / "node" / "vrm"
VRM_BUNDLE = VRM_DIR / "dist" / "vrm.js"
RENDERER = ROOT / "node" / "scene3d" / "render.mjs"
CONTACT = VRM_DIR / "contact.mjs"
TIMEOUT_SEC = 3 * 3600
CHECK_TIMEOUT_SEC = 3600


class RenderFailed(RuntimeError):
    """Рендер или проверки не дали годного ролика (причина — для человека)."""

    def __init__(self, message: str, checks: list[dict] | None = None):
        super().__init__(message)
        self.checks = checks or []


# --------------------------------------------------------------------------- узлы node (тесты подменяют)


def vrm_dir() -> Path:
    return Path(settings.series3d_vrm_dir)


def ensure_vrm_bundle() -> Path:
    """Бандл VRM-движка (в образе собран при сборке; локально — esbuild из node_modules)."""
    if VRM_BUNDLE.is_file():
        return VRM_BUNDLE
    esbuild = ROOT / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not esbuild.exists():
        raise RenderFailed(f"нет бандла {VRM_BUNDLE}: npm ci && npm run build:vrm")
    proc = subprocess.run([str(esbuild), str(VRM_DIR / "src" / "main.ts"), "--bundle", "--format=iife", "--platform=browser",
                           "--target=chrome110", "--minify", f"--outfile={VRM_BUNDLE}", "--log-level=warning"],
                          capture_output=True, text=True, check=False, cwd=ROOT, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not VRM_BUNDLE.is_file():
        raise RenderFailed(f"сборка VRM-движка: {proc.stderr.strip()[-400:]}")
    return VRM_BUNDLE


def _gl() -> str:
    return os.environ.get("SCENE3D_GL", "swiftshader")


def _last_json(text: str) -> dict | None:
    for line in reversed((text or "").strip().splitlines()):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def render_vrm(spec_path: Path, out: Path) -> dict:
    """Спека VRM → mp4. {code, report, error}: report — отчёт render.mjs (кадры, чёрные, присутствие)."""
    from app.scene3d import chromium_path, node_bin

    ensure_vrm_bundle()
    args = [node_bin(), str(RENDERER), "--spec", str(spec_path), "--out", str(out), "--bundle", str(VRM_BUNDLE),
            "--packs", str(vrm_dir()), "--gl", _gl(), "--workers", "1"]
    chrome = chromium_path()
    if chrome:
        args += ["--chrome", chrome]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SEC,
                              check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"code": -1, "report": None, "error": f"рендер не уложился в {TIMEOUT_SEC // 60} мин"}
    return {"code": proc.returncode, "report": _last_json(proc.stdout),
            "error": " ".join((proc.stderr or "").strip().splitlines()[-4:])[-1200:]}


def check_vrm(spec_path: Path) -> dict | None:
    """Проверки касаний по всем кадрам (пальцы, кресло, волосы, руки против тела) — отчёт contact.mjs."""
    from app.scene3d import node_bin

    try:
        proc = subprocess.run([node_bin(), str(CONTACT), "--spec", str(spec_path), "--packs", str(vrm_dir()),
                               "--bundle", str(VRM_BUNDLE), "--gl", _gl()], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=CHECK_TIMEOUT_SEC, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"error": "проверки касаний не уложились во время"}
    return _last_json(proc.stdout) or {"error": (proc.stderr or "").strip()[-400:] or f"код {proc.returncode}"}


def contact_sheet(video: Path, out: Path) -> Path:
    from app.scene3d import contact_sheet as sheet

    return sheet(video, out, count=12, cols=4, width=270)


# --------------------------------------------------------------------------- проверки


def build_checks(spec: dict, render: dict, contact: dict | None, words_info: dict, sheet: Path | None) -> list[dict]:
    """Проверки ролика для карточки серии. ok: True/False; detail — что именно."""
    from app import vtubika_vrm as VRM

    rep = render.get("report") or {}
    checks: list[dict] = []
    err = str(render.get("error") or "")
    rendered = render.get("code") == 0 and bool(rep)
    black = rep.get("black_frames") or []
    checks.append({"id": "render", "label": "3D отрисовано", "ok": rendered,
                   "detail": (f"{rep.get('frames')} кадров за {rep.get('seconds')} с" if rendered
                              else (err or "рендер не вернул отчёт")[-500:])})
    checks.append({"id": "black", "label": "без чёрных кадров", "ok": bool(rep) and not black,
                   "detail": (f"чёрные кадры: {black[:12]}" if black else ("нет" if rep else "не измерено"))})
    probs = VRM.render_problems(rep if rep else None, spec) if rendered else []
    cut = [p for p in probs if "разрезан" in p] + (["персонаж разрезан"] if "разрезан" in err else [])
    pres = [p for p in probs if "разрезан" not in p]
    if not rendered and ("модели нет" in err or "холст пуст" in err or "контекст GPU" in err):
        pres.append(err[-300:])
    pres_info = rep.get("presence") or {}
    checks.append({"id": "presence", "label": "героиня в кадре", "ok": rendered and not pres and bool(pres_info),
                   "detail": "; ".join(pres) or (f"проверено {pres_info.get('frames')} кадров, нет героини — "
                                                 f"{pres_info.get('missing', 0)}" if pres_info else "не измерено")})
    checks.append({"id": "cut", "label": "не разрезана краем кадра", "ok": rendered and not cut and bool(pres_info),
                   "detail": "; ".join(cut) or ("ровных срезов нет" if pres_info else "не измерено")})
    empty = VRM.check_problems(contact, spec) if rendered else ["рендер не прошёл — касания не проверены"]
    hands_bad = int((contact or {}).get("hand_spans") or 0)
    fingers = ((contact or {}).get("fingers") or {}).get("min_gap_mm")
    contact_ok = bool(contact) and not empty and bool(contact.get("ok"))
    detail = "; ".join(empty) if empty else (
        f"пальцы над столом: {fingers} мм, руки сквозь тело: {hands_bad} отрезков" if contact else "не измерено")
    checks.append({"id": "contact", "label": "руки и касания", "ok": contact_ok, "detail": detail, "empty": bool(empty)})
    src = words_info.get("source")
    checks.append({"id": "words", "label": "субтитры по голосу", "ok": bool(src == "whisper" or words_info.get("dry")),
                   "detail": ("по распознанной речи" if src == "whisper" else
                              "черновик: тон вместо речи, слова по длине текста" if words_info.get("dry") else
                              f"Whisper не распознал ({words_info.get('error') or words_info.get('heard_share')}) — слова по длине текста")})
    checks.append({"id": "sheet", "label": "лист кадров", "ok": bool(sheet and sheet.is_file()),
                   "detail": "12 кадров" if sheet and sheet.is_file() else "не собран"})
    return checks


def fatal(checks: list[dict]) -> list[str]:
    """Что не даёт выпустить клип: 3D не отрисовано, героини нет, пустые проверки."""
    out = []
    for c in checks:
        if c["id"] in ("render", "presence", "cut") and not c["ok"]:
            out.append(f"{c['label']}: {c['detail']}")
        if c["id"] == "contact" and c.get("empty"):
            out.append(f"{c['label']}: {c['detail']}")
    return out


# --------------------------------------------------------------------------- шаги задания


def _plan_for(store: Any, job: dict) -> tuple[dict, dict, dict, dict]:
    ep = D.get_episode(store, int(job["episode_id"]))
    pid = int(ep["project_id"])
    lib, cfg = D.library(store, pid), D.get_settings(store, pid)
    raw = D.plan_at(store, int(job["episode_id"]), int(job["plan_version"]))
    plan, _w = validate_plan(raw, lib, cfg)
    return ep, plan, lib, cfg


def _fail(store: Any, job: dict, error: str, **result: Any) -> dict:
    res = {**(job.get("result") or {}), **result}
    D.set_job(store, job["id"], "failed", expect=D.JOB_ACTIVE, error=str(error)[:1500], result=res, note="")
    return D.get_job(store, job["id"])


def step_queued(store: Any, job: dict) -> str:
    try:
        _ep, plan, _lib, _cfg = _plan_for(store, job)
    except PlanError as exc:
        _fail(store, job, "план не проходит проверку: " + "; ".join(exc.problems[:6]))
        return "failed"
    if plan["engine"] not in RENDERABLE:
        _fail(store, job, f"движок {plan['engine']} в сервисе пока не рисуется (готов vrm_desk)")
        return "failed"
    D.set_job(store, job["id"], "voice", expect=("queued",), note="записываю голос реплик")
    return "next"


def step_voice(store: Any, job: dict) -> str:
    from app import s3d_voice as SV

    dry = bool(job["options"].get("dry_run"))
    state = SV.readiness(store, int(job["episode_id"]), dry)
    if state["ready"]:
        D.set_job(store, job["id"], "render", expect=("voice",), note="жду места в очереди тяжёлых задач")
        return "next"
    if state["failed"] and job["result"].get("voice_requested"):
        _fail(store, job, "голос не записался: " + "; ".join(state["failed"])[:600])
        return "failed"
    if state["active"]:
        D.set_job(store, job["id"], note=f"записываю голос: осталось {len(state['missing'])} реплик")
        return "wait"
    if state["held"]:
        D.set_job(store, job["id"], note="голос ждёт запуска: " + "; ".join(state["held"][:2]))
        return "wait"
    try:
        req = SV.request(store, int(job["episode_id"]), requested_by=job.get("requested_by") or "agent")
    except Exception as exc:  # noqa: BLE001 - потолок, нет ключа — владельцу понятный текст
        _fail(store, job, f"голос не заказан: {exc}")
        return "failed"
    D.set_job(store, job["id"], result={**job["result"], "voice_requested": True,
                                        "voice_estimate_rub": (req.get("estimate") or {}).get("total_rub", 0)})
    if req.get("status") in ("awaiting_approval", "approved") and req.get("approval_id"):
        est = float((req.get("estimate") or {}).get("total_rub") or 0)
        if req["status"] == "awaiting_approval":
            D.set_job(store, job["id"], "awaiting_approval", expect=("voice",), approval_id=int(req["approval_id"]),
                      estimate_rub=est, note=f"карточка #{req['approval_id']}: платный голос ≈ {est:.2f} ₽ — ждёт «Разрешить»")
            return "wait"
        D.set_job(store, job["id"], approval_id=int(req["approval_id"]), estimate_rub=est)
    again = SV.readiness(store, int(job["episode_id"]), dry)
    if again["ready"]:
        D.set_job(store, job["id"], "render", expect=("voice",), note="жду места в очереди тяжёлых задач")
        return "next"
    if again["failed"]:
        _fail(store, job, "голос не записался: " + "; ".join(again["failed"])[:600])
        return "failed"
    return "wait"


def step_approval(store: Any, job: dict) -> str:
    from app import autopilot as A

    card = A.get_approval(store, int(job["approval_id"]))
    if card["status"] == "pending":
        return "wait"
    if card["status"] != "approved":
        D.set_job(store, job["id"], "cancelled", expect=("awaiting_approval",),
                  error=f"карточка #{card['id']}: {card['status']}" + (f" — {card.get('decision_comment')}"
                                                                     if card.get("decision_comment") else ""))
        return "cancelled"
    D.set_job(store, job["id"], "voice", expect=("awaiting_approval",), note="разрешено — записываю платный голос")
    return "next"


def step_render(store: Any, job: dict) -> str:
    from app.browser_lock import browser_slot

    label = f"3D-серия {job['episode_id']} (задание {job['id']})"
    with browser_slot("series3d", label):
        if D.get_job(store, job["id"])["status"] != "render":
            return "wait"  # пока ждали места — отменили
        D.set_job(store, job["id"], note="рисую 3D", progress=0.1)
        try:
            result = produce(store, D.get_job(store, job["id"]))
        except RenderFailed as exc:
            _fail(store, D.get_job(store, job["id"]), str(exc), checks=exc.checks)
            return "failed"
        except PlanError as exc:
            _fail(store, D.get_job(store, job["id"]), "план: " + "; ".join(exc.problems[:6]))
            return "failed"
        except Exception as exc:  # noqa: BLE001 - причину — в карточку, работник живёт
            _log.warning("series3d job %s failed", job["id"], exc_info=True)
            _fail(store, D.get_job(store, job["id"]), f"{type(exc).__name__}: {str(exc)[:600]}")
            return "failed"
    D.set_job(store, job["id"], "done", expect=("render",), result=result, progress=1.0,
              note="готово" + ("" if result.get("checks_ok") else " — есть замечания проверок"))
    return "done"


STEPS = {"queued": step_queued, "voice": step_voice, "awaiting_approval": step_approval, "render": step_render}
_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(job_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(job_id), threading.Lock())


def advance(store: Any, job_id: int, max_steps: int = 6) -> dict:
    """Довести задание насколько можно сейчас (ждём владельца/голос — выходим)."""
    lock = _lock(job_id)
    if not lock.acquire(blocking=False):
        return D.get_job(store, job_id)  # этим заданием уже занят другой поток
    try:
        for _ in range(max_steps):
            job = D.get_job(store, job_id)
            step = STEPS.get(job["status"])
            if step is None or step(store, job) != "next":
                break
        return D.get_job(store, job_id)
    finally:
        lock.release()


def start(store: Any, episode_id: int, options: dict | None = None, requested_by: str = "agent", run: bool = False) -> dict:
    """«Рендер»: новое задание (идущее — то же). ``run`` — сразу вести (тесты, сухой прогон), иначе — работник."""
    cur = D.active_job(store, episode_id)
    if cur is not None:
        return cur
    opts = {k: v for k, v in dict(options or {}).items() if k in ("dry_run", "render_clip")}
    job = D.create_job(store, episode_id, opts, requested_by)
    if run:
        return advance(store, int(job["id"]))
    kick()
    return job


def cancel(store: Any, episode_id: int) -> dict | None:
    job = D.active_job(store, episode_id)
    if job is None:
        return None
    D.set_job(store, job["id"], "cancelled", expect=D.JOB_ACTIVE, error="отменено")
    if job.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'серия отменена' WHERE approval_id = ? "
                         "AND status = 'awaiting'", (int(job["approval_id"]),))
    return D.get_job(store, job["id"])


# --------------------------------------------------------------------------- сам рендер


def produce(store: Any, job: dict) -> dict:
    """Голос → слова → рот → спека → 3D → проверки → лист → холст-клип. Возвращает итог для карточки серии."""
    from app import s3d_clip as C
    from app import s3d_voice as SV
    from app.canvas import base_seconds
    from app.s3d_vrm import compile_vrm, speaker

    started = time.time()
    ep, plan, lib, cfg = _plan_for(store, job)
    dry = bool(job["options"].get("dry_run"))
    state = SV.readiness(store, int(job["episode_id"]), dry)
    if not state["ready"]:
        raise RenderFailed("голос реплик ещё не готов: " + ", ".join(state["missing"]))
    paths = {ln["id"]: ln["path"] for ln in state["lines"]}
    work = settings.tmp_dir / f"s3d-{job['id']}-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    keep = os.environ.get("SERIES3D_KEEP_WORK") == "1"
    try:
        track = work / "voice.wav"
        spans = SV.build_track(plan["lines"], paths, float(plan["lead"]), track)
        real = any(ln["voice"]["provider"] != "none" for ln in state["lines"])
        words, info = SV.timed_words(plan["lines"], spans, track, real)
        info["dry"] = not real
        total = round(spans[-1]["end"] + float(plan["tail"]), 3)
        if total > base_seconds():
            raise RenderFailed(f"серия {total:.1f} с длиннее холста {base_seconds():.0f} с")
        hero = plan["cast"][0]
        hero_lines = {ln["id"] for ln in plan["lines"] if speaker(plan, ln) == hero}
        windows = [(s["start"], s["end"]) for s in spans if s["id"] in hero_lines]
        envelope = SV.mouth_envelope(track, windows, total) if windows else []
        compiled = compile_vrm(plan, lib, words, total, envelope=envelope)
        spec = compiled["spec"]
        spec_path = work / "scene_vrm.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
        D.set_job(store, job["id"], note=f"рисую 3D: {round(total * 30)} кадров", progress=0.25)
        raw = work / "scene_vrm.mp4"
        t3d = time.time()
        render = render_vrm(spec_path, raw)
        render_sec = round(time.time() - t3d, 1)
        ok = render.get("code") == 0 and raw.is_file()
        contact = None
        if ok:
            D.set_job(store, job["id"], note="проверяю руки, касания и кадр", progress=0.7)
            contact = check_vrm(spec_path)
        sheet = None
        if ok:
            try:
                sheet = contact_sheet(raw, work / "sheet.jpg")
            except Exception as exc:  # noqa: BLE001 - без листа — провал проверки «лист кадров»
                _log.warning("series3d sheet failed: %s", exc)
        checks = build_checks(spec, render, contact, info, sheet)
        bad = fatal(checks)
        if bad:
            raise RenderFailed("ролик не выпускаю: " + "; ".join(bad)[:900], checks)
        D.set_job(store, job["id"], note="собираю клип", progress=0.85)
        clip = C.assemble(store, ep, plan, compiled, track, words, raw, sheet, total, job)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    checks_ok = all(c["ok"] for c in checks)
    return {"clip_id": clip["clip_id"], "editor_url": f"/clips/{clip['clip_id']}", "video_asset_id": clip["video_asset_id"],
            "sheet_asset_id": clip.get("sheet_asset_id"), "sheet_url": clip.get("sheet_url"),
            "video_url": clip.get("video_url"), "checks": checks, "checks_ok": checks_ok, "duration": total,
            "render_sec": render_sec, "total_sec": round(time.time() - started, 1),
            "frames": (render.get("report") or {}).get("frames"), "voices": SV.voice_summary(state["lines"]),
            "words_source": info.get("source"), "lines": compiled["lines"], "shots": compiled["shots"],
            "applied": {k: v for k, v in compiled["applied"].items() if k != "light"} | {"light": bool(compiled["applied"].get("light"))},
            "warnings": compiled["warnings"] + clip.get("notes", []), "plan_version": int(job["plan_version"]),
            "voice_requested": True, "version": clip.get("version")}


# --------------------------------------------------------------------------- работник

_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class Series3dWorker:
    """Фон: ведёт задания рендера 3D-серий (голос, ожидание карточки, рендер по одному)."""

    IDLE_SECONDS = 5

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        global _wake
        if os.getenv("SERIES3D_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="series3d", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        done = []
        for jid in D.active_jobs(self.store):
            advance(self.store, jid)
            done.append(jid)
        return done

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("series3d worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()


def recover_after_restart(store: Any) -> int:
    """Задание, которое рисовалось в прошлом процессе, снова ждёт места в очереди (рендер повторяемый)."""
    cur = store.db.execute("UPDATE s3d_jobs SET note = 'сервис перезапускался — рисую заново', progress = 0 "
                           "WHERE status = 'render'")
    return int(cur.rowcount or 0)
