"""Шаги AMV поверх инструмента ``tools/amv``: разбор песни, библиотека сцен исходников, план, рендер в клип проекта.

Файлы работы — ``<runtime>/amv/<amv_id>/`` (app/amv.py): ``song.json``, ``lib/`` (кэш сканов, clips.json,
boxes.json), ``sources.json``, ``overrides.json``, ``story_<план>.json``, ``plan_<план>.json``, ``out/``.
Состояние заданий — в ``amv.json`` (``jobs``): статус переживает перезапуск (незаконченное — ``interrupted``).

* разбор песни — ``tools.amv.audio.analyze_song`` в этом процессе (numpy, секунды на песню);
* библиотека и рендер — подпроцессом ``python -m tools.amv.make lib|render|check`` (своя память; отмена гасит только
  свой PID) в очереди тяжёлых задач (``heavy_slot``), задания AMV — по одному;
* план — чистая логика (``tools.amv.style``: приоритеты референса), считается сразу.

Готовый рендер — клип проекта (app/clip_upload.register_file_clip); полный (не проба) — ещё и версия в ленте
(app/feed.py, вид amv).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app import amv as A

_log = logging.getLogger(__name__)

SYNC = False  # тесты: задание целиком внутри вызова (без потока)
_RUN = threading.Lock()  # одно тяжёлое задание AMV за раз
_ACTIVE: set[tuple[str, str]] = set()
_PROCS: dict[tuple[str, str], subprocess.Popen] = {}
LIB_TIMEOUT_SEC = 4 * 3600
RENDER_TIMEOUT_SEC = 3 * 3600
TEST_SEC = 20.0


class AmvError(ValueError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def tool(name: str) -> Any:
    """Модуль tools/amv (пакет; в образе — /app/tools/amv)."""
    import importlib

    root = str(repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(f"tools.amv.{name}")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------- #
# Задания
# --------------------------------------------------------------------------- #


def job_state(manifest: dict, kind: str) -> dict:
    job = dict((manifest.get("jobs") or {}).get(kind) or {})
    if job.get("status") in ("queued", "running") and (manifest["id"], kind) not in _ACTIVE:
        job.update(status="interrupted", error="сервис перезапускался — задание прервано, позови шаг снова "
                                               "(готовое в кэше: сканы, разбор)")
    return job


def _set_job(amv_id: str, kind: str, **fields: Any) -> dict:
    def fn(m: dict) -> None:
        job = m.setdefault("jobs", {}).setdefault(kind, {})
        job.update(fields, updated_at=round(time.time(), 1))

    return A.update(amv_id, fn)["jobs"][kind]


def submit(amv_id: str, kind: str, label: str, body: Callable[[], dict]) -> dict:
    """Задание в фон (или сразу при SYNC). Идёт такое же — вернуть его состояние."""
    key = (amv_id, kind)
    if key in _ACTIVE:
        return job_state(A.load(amv_id), kind)
    _ACTIVE.add(key)
    _set_job(amv_id, kind, status="queued", message="в очереди", error="", result=None,
             started_at=round(time.time(), 1), finished_at=None)

    def run() -> None:
        from app.heavy_queue import heavy_slot

        try:
            with _RUN, heavy_slot("amv", f"AMV {amv_id}: {label}"):
                _set_job(amv_id, kind, status="running", message=label)
                result = body()
            _set_job(amv_id, kind, status="done", message="готово", result=result, finished_at=round(time.time(), 1))
        except Exception as exc:  # noqa: BLE001 - любой сбой — статус задания
            _log.warning("amv %s %s failed: %s", amv_id, kind, exc)
            text = str(exc) if isinstance(exc, (KeyError, ValueError, RuntimeError)) else f"{type(exc).__name__}: {exc}"
            _set_job(amv_id, kind, status="failed", error=text[:800], finished_at=round(time.time(), 1))
        finally:
            _ACTIVE.discard(key)

    if SYNC:
        run()
    else:
        threading.Thread(target=run, name=f"amv-{amv_id}-{kind}", daemon=True).start()
    return job_state(A.load(amv_id), kind)


def wait(amv_id: str, kind: str, seconds: float) -> dict:
    end = time.monotonic() + max(0.0, seconds)
    while True:
        job = job_state(A.load(amv_id), kind)
        if job.get("status") not in ("queued", "running") or time.monotonic() >= end:
            return job
        time.sleep(1.0)


def _make(amv_id: str, kind: str, args: list[str], log_name: str, timeout: int) -> None:
    """``python -m tools.amv.make …`` подпроцессом, лог в папку работы; отмена/таймаут — только свой PID."""
    folder = A.work_dir(amv_id)
    log = folder / log_name
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "PYTHONPATH": os.pathsep.join(x for x in (str(repo_root()), os.environ.get("PYTHONPATH", "")) if x)}
    started = time.monotonic()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen([sys.executable, "-m", "tools.amv.make", *args], stdout=fh, stderr=subprocess.STDOUT,
                                cwd=str(repo_root()), env=env)
        _PROCS[(amv_id, kind)] = proc
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(f"{args[0]}: дольше {timeout // 60} мин — остановлено (лог {log.name})")
                time.sleep(1.0)
        finally:
            _PROCS.pop((amv_id, kind), None)
    if proc.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise RuntimeError(f"{args[0]} упал ({proc.returncode}): " + " | ".join(tail)[-600:])


def cancel(amv_id: str) -> list[str]:
    """Погасить идущие подпроцессы этой работы (по их PID)."""
    stopped = []
    for (aid, kind), proc in list(_PROCS.items()):
        if aid == amv_id and proc.poll() is None:
            proc.kill()
            stopped.append(kind)
    return stopped


# --------------------------------------------------------------------------- #
# 1. Песня
# --------------------------------------------------------------------------- #


def song_path(store: Any, manifest: dict) -> Path:
    track = store.get_audio_track(int(manifest["track_id"]))
    path = Path(str(track.get("file_path") or ""))
    if not path.is_file():
        raise AmvError(f"у трека #{track['id']} нет файла")
    return path


def analyze_song(store: Any, amv_id: str) -> dict:
    audio, media = tool("audio"), tool("media")
    manifest = A.load(amv_id)
    x = media.load_audio(song_path(store, manifest), 22050)
    song = audio.analyze_song(x, 22050)
    (A.work_dir(amv_id) / "song.json").write_text(json.dumps(song), encoding="utf-8")
    summary = song_summary(song)
    A.update(amv_id, lambda m: m.update(song=summary))
    return summary


def song_summary(song: dict) -> dict:
    """Для агента: темп, дропы, части по громкости (тихо → story, громко → action, последний дроп → climax), тихие
    провалы перед дропами (там будет «вдох» в чёрное), уровни тактов строкой."""
    style = tool("style")
    view = style.musical(song)
    parts = style.sections(view, 0.0, float(song["duration"]))
    breaths = style.black_breaths(song, 0.0, float(song["duration"]))
    levels = "".join(str(b.get("level", 0)) for b in view["bars"])
    return {"duration": round(float(song["duration"]), 2), "bpm": song["bpm"],
            "felt_bpm": view["bpm"], "half_time": bool(view.get("half_time")),
            "drops": [round(float(d), 2) for d in song.get("drops", [])],
            "parts": [{"t0": round(p["t0"], 2), "t1": round(p["t1"], 2), "mode": p["mode"]} for p in parts],
            "breaths": [{"t0": b["t0"], "t1": b["t1"], "kind": b["kind"]} for b in breaths],
            "bar_levels": levels, "bar_sec": round(4 * float(view["period"]), 3)}


# --------------------------------------------------------------------------- #
# 2. Библиотека сцен
# --------------------------------------------------------------------------- #


def _spans(value: Any, with_name: bool) -> list:
    out = []
    for item in value or []:
        if not isinstance(item, (list, tuple)) or len(item) < (3 if with_name else 2):
            raise AmvError("отрезки — [начало, конец" + (", акт]" if with_name else "]") + " в секундах исходника")
        a, b = float(item[0]), float(item[1])
        if b <= a:
            raise AmvError(f"отрезок {item}: конец раньше начала")
        out.append([a, b, str(item[2]).strip().lower()] if with_name else [a, b])
    return out


def sources_json(store: Any, manifest: dict, specs: list[dict] | None) -> list[dict]:
    """sources.json инструмента из видео работы + разметки агента (exclude, acts, box) по source_id."""
    from app import nav

    by_id = {int(s.get("source_id")): s for s in specs or [] if isinstance(s, dict) and s.get("source_id")}
    unknown = [s for s in by_id if s not in manifest["source_ids"]]
    if unknown:
        raise AmvError(f"видео {unknown} нет в работе — исходники работы: {manifest['source_ids']}")
    out = []
    for sid in manifest["source_ids"]:
        source = store.get_source(int(sid))
        path = Path(str(source.get("local_path") or ""))
        if not path.is_file():
            raise AmvError(f"у видео #{sid} нет файла (не скачано?)")
        spec = by_id.get(int(sid), {})
        item = {"film": f"s{sid}", "path": str(path), "source_id": int(sid),
                "credit": nav.titles_for(store, [source]).get(int(sid), "") or str(source.get("original_filename") or ""),
                "exclude": _spans(spec.get("exclude"), False), "acts": _spans(spec.get("acts"), True)}
        if spec.get("box"):
            box = [int(v) for v in spec["box"]]
            if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
                raise AmvError("box — [x, y, ширина, высота] картинки без полос и плашек")
            item["box"] = box
        out.append(item)
    return out


def index_sources(store: Any, amv_id: str, specs: list[dict] | None) -> dict:
    manifest = A.load(amv_id)
    folder = A.work_dir(amv_id)
    srcs = sources_json(store, manifest, specs)
    (folder / "sources.json").write_text(json.dumps(srcs, ensure_ascii=False, indent=1), encoding="utf-8")
    _make(amv_id, "index", ["lib", "--sources", str(folder / "sources.json"), "--work", str(folder)], "lib.log",
          LIB_TIMEOUT_SEC)
    summary = library_summary(_read(folder / "lib" / "clips.json"), srcs)
    A.update(amv_id, lambda m: m.update(library=summary))
    return summary


def library_summary(clips: list[dict], srcs: list[dict]) -> dict:
    import numpy as np

    films: dict[str, dict] = {}
    for c in clips:
        f = films.setdefault(c["film"], {"clips": 0, "impacts": 0, "acts": {}})
        f["clips"] += 1
        f["impacts"] += len(c.get("impacts") or [])
        act = c.get("act") or "-"
        f["acts"][act] = f["acts"].get(act, 0) + 1
    shots: dict[str, list[float]] = {}
    for c in clips:
        s = shots.setdefault(c.get("shot") or c["id"].rsplit(":", 1)[0], [c["t_in"], c["t_out"]])
        s[0], s[1] = min(s[0], c["t_in"]), max(s[1], c["t_out"])
    long_shots = sum(1 for a, b in shots.values() if b - a >= 3.0)
    labs = np.array([c["lab"] for c in clips if c.get("lab") is not None] or [[0, 0, 0]], float)
    chroma = np.hypot(labs[:, 1], labs[:, 2])
    by_src = {f"s{s['source_id']}": s for s in srcs}
    notes = []
    if not any(s.get("acts") for s in srcs):
        notes.append("акты не размечены — сюжетная арка (story) не сможет держать порядок истории: отметь acts по "
                     "секундам исходника (source_contact_sheet) и позови amv_index_sources снова (сканы в кэше)")
    if long_shots < 6:
        notes.append("длинных планов (≥ 3 с) мало — тихие части пойдут замедлением 0.45–0.6×; лучше добавить "
                     "исходник подлиннее (фильм/серия, а не нарезку трейлера)")
    return {
        "clips": len(clips), "shots": len(shots), "long_shots": long_shots,
        "duplicates": sum(1 for c in clips if c.get("dup") and c["dup"] != c["id"]),
        "films": {k: {"source_id": by_src.get(k, {}).get("source_id"), "title": by_src.get(k, {}).get("credit", ""),
                      "clips": v["clips"], "impacts": v["impacts"], "acts": v["acts"]} for k, v in films.items()},
        "colour": {"L_p10": round(float(np.percentile(labs[:, 0], 10)), 1),
                   "L_p90": round(float(np.percentile(labs[:, 0], 90)), 1),
                   "chroma_median": round(float(np.median(chroma)), 1)},
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# 3. План
# --------------------------------------------------------------------------- #

OVERRIDE_KEYS = ("ban", "ban_films", "boost", "act", "motif", "face", "cx_916", "cx_169")


def merge_overrides(amv_id: str, patch: dict | None) -> dict:
    path = A.work_dir(amv_id) / "overrides.json"
    current = _read(path) if path.is_file() else {}
    for key, value in (patch or {}).items():
        if key not in OVERRIDE_KEYS:
            raise AmvError(f"overrides: {key}? — можно {', '.join(OVERRIDE_KEYS)}")
        if isinstance(value, list):
            current[key] = list(dict.fromkeys([*current.get(key, []), *value]))
        elif isinstance(value, dict):
            current[key] = {**current.get(key, {}), **value}
        else:
            raise AmvError(f"overrides.{key} — список или словарь")
    path.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
    return current


def test_window(song: dict, length: float = TEST_SEC) -> tuple[float, float]:
    """Проба: 20 с вокруг последнего дропа во второй половине (кульминация видна сразу), иначе — самого громкого."""
    dur = float(song["duration"])
    drops = [float(d) for d in song.get("drops", [])]
    late = [d for d in drops if d >= 0.45 * dur] or drops
    if late:
        d = late[-1]
    else:
        d = max(song["bars"], key=lambda b: b.get("energy", 0))["t"] if song.get("bars") else dur / 2
    t0 = max(0.0, min(dur - length, d - 0.3 * length))
    return round(t0, 3), round(min(dur, t0 + length), 3)


def _story(value: Any) -> list[dict] | None:
    if value in (None, "", []):
        return None
    if not isinstance(value, list):
        raise AmvError("story — список частей [{t0, t1, acts, mode, colour, note}] в секундах песни")
    out = []
    for p in value:
        if not isinstance(p, dict) or p.get("t0") is None or p.get("t1") is None:
            raise AmvError("часть story — {t0, t1, acts?, mode?, colour?, note?}")
        mode = p.get("mode")
        if mode not in (None, "", "story", "action", "climax"):
            raise AmvError("mode части — story | action | climax")
        out.append({"t0": float(p["t0"]), "t1": float(p["t1"]), "acts": [str(a).lower() for a in p.get("acts") or []],
                    **({"mode": mode} if mode else {}), **({"colour": p["colour"]} if p.get("colour") else {}),
                    "note": str(p.get("note") or "")[:200]})
    return out


def make_plan(amv_id: str, *, name: str = "test", start: float | None = None, end: float | None = None,
              test: bool = False, story: Any = None, overrides: dict | None = None, seed: int = 7, reuse: int = 1,
              strobe: bool = True, breaths: bool = True) -> dict:
    plan_mod, style, library = tool("plan"), tool("style"), tool("library")
    folder = A.work_dir(amv_id)
    if not (folder / "song.json").is_file():
        raise AmvError("сначала amv_analyze_song")
    if not (folder / "lib" / "clips.json").is_file():
        raise AmvError("сначала amv_index_sources")
    if not str(name or "").replace("_", "").replace("-", "").isalnum() or len(str(name)) > 32:
        raise AmvError("name — латиница/цифры, до 32 знаков (test, full, v2…)")
    song = _read(folder / "song.json")
    clips = library.apply_overrides(_read(folder / "lib" / "clips.json"), merge_overrides(amv_id, overrides))
    if test and start is None and end is None:
        t0, t1 = test_window(song)
    else:
        t0 = max(0.0, float(start or 0.0))
        t1 = min(float(song["duration"]), float(end) if end is not None else float(song["duration"]))
    if t1 - t0 < 4.0:
        raise AmvError("участок короче 4 с")
    parts = _story(story)
    if parts is None and (folder / "story.json").is_file():
        parts = _read(folder / "story.json")
    elif parts is not None:
        (folder / "story.json").write_text(json.dumps(parts, ensure_ascii=False, indent=1), encoding="utf-8")
    p = plan_mod.build_plan(song, clips, t0, t1, seed=int(seed), story=parts, max_uses=max(1, int(reuse)),
                            style="reference", strobe="last" if strobe else "none", breaths=bool(breaths))
    p["stats"] = style.stats(p, song)
    (folder / f"plan_{name}.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    view = plan_view(name, p)
    A.update(amv_id, lambda m: m.setdefault("plans", {}).update(
        {name: {"t0": t0, "t1": t1, "stats": {k: v for k, v in p["stats"].items() if k != "checks"},
                "checks": p["stats"]["checks"], "created_at": round(time.time(), 1)}}))
    return view


def plan_view(name: str, p: dict, limit: int = 60) -> dict:
    shots = []
    for s in p["segments"][:limit]:
        flags = [k for k in ("drop", "hero", "strobe", "relaxed") if s.get(k)]
        if s.get("xfade"):
            flags.append(f"dissolve {s['xfade']:g}s")
        if s.get("use_impacts") and s.get("impact_shown"):
            flags.append("own impact")
        if s.get("motif"):
            flags.append(f"motif {s['motif']}")
        shots.append({"t": round(s["t0"], 2), "dur": round(s["dur"], 2), "mode": s.get("mode"),
                      "clip": s.get("clip"), "act": s.get("act"),
                      "src_in": None if s.get("src_in") is None else round(s["src_in"], 2),
                      "speed": round(float((s.get("ramp") or {}).get("base", 1.0)), 2), "flags": flags})
    stats = p["stats"]
    failed = [c["name"] for c in stats["checks"] if c["ok"] is False]
    return {"name": name, "t0": p["t0"], "t1": p["t1"], "sections": p.get("sections"),
            "stats": {k: v for k, v in stats.items() if k not in ("checks", "sections")}, "checks": stats["checks"],
            "failed_checks": failed, "blacks": p.get("blacks"), "shots": shots,
            "shots_more": max(0, len(p["segments"]) - limit)}


# --------------------------------------------------------------------------- #
# 4. Рендер
# --------------------------------------------------------------------------- #


def render(store: Any, amv_id: str, name: str, aspect: str, final: bool) -> dict:
    from app.clip_upload import register_file_clip

    manifest = A.load(amv_id)
    folder = A.work_dir(amv_id)
    plan_file = folder / f"plan_{name}.json"
    if not plan_file.is_file():
        raise AmvError(f"нет плана {name} — сначала amv_plan(name: {name!r})")
    w, h = (A.ASPECTS if final else A.TEST_SIZES)[aspect]
    out_dir = folder / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{name}_{aspect.replace(':', 'x')}"
    out = out_dir / f"{amv_id}_{tag}.mp4"
    p = _read(plan_file)
    full = float(p["t1"]) >= float(_read(folder / "song.json")["duration"]) - 1.0
    _make(amv_id, f"render:{tag}", ["render", "--work", str(folder), "--name", name, "--song",
                                    str(song_path(store, manifest)), "--out", str(out), "--size", f"{w}x{h}",
                                    "--fps", "30", "--fade", "0,3" if full else "0,0.4"], f"render_{tag}.log",
          RENDER_TIMEOUT_SEC)
    _make(amv_id, f"render:{tag}", ["check", "--video", str(out), "--work", str(folder)], f"check_{tag}.log", 1800)
    check = _read(folder / f"{out.stem}_check.json")
    sheet = folder / f"{out.stem}_sheet.jpg"
    kind = "полная версия" if final else "проба"
    desc = (f"AMV «{manifest['title']}» ({kind}, {aspect}): {p['t1'] - p['t0']:.0f} с песни «{manifest['track_label']}» "
            f"с {p['t0']:.1f} с")
    clip = register_file_clip(store, out, f"AMV · {manifest['title']}" + ("" if final else f" · {name}"), desc,
                              int(manifest["project_id"]))
    result = {"clip_id": clip["clip_id"], "clip_plan_id": clip["clip_plan_id"], "to": f"/clips/{clip['clip_plan_id']}",
              "aspect": aspect, "size": f"{w}x{h}", "duration": round(float(check.get("duration") or 0), 2),
              "frozen": check.get("frozen_stretches") or [], "sheet": str(sheet) if sheet.is_file() else None,
              "final": bool(final)}
    if final:
        result["feed"] = to_feed(store, amv_id, clip["clip_id"], aspect, p)
    A.update(amv_id, lambda m: m.setdefault("renders", {}).update({tag: result}))
    return result


def to_feed(store: Any, amv_id: str, clip_id: int, aspect: str, p: dict) -> dict:
    """Полная версия — новой версией работы в ленте (вид amv), на проверку владельцу."""
    from app import feed as FEED

    manifest = A.load(amv_id)
    try:
        work_id = manifest.get("feed_work_id")
        if not work_id:
            work_id = FEED.create_work(store, f"AMV · {manifest['title']}", manifest["project_id"], kind="amv",
                                       made_by="agent")["id"]
            A.update(amv_id, lambda m: m.update(feed_work_id=int(work_id)))
        checks = [c["name"] for c in (p.get("stats") or {}).get("checks", []) if c.get("ok") is False]
        note = (f"Песня «{manifest['track_label']}», {p['t1'] - p['t0']:.0f} с; приоритеты референса"
                + (f"; не в норме: {', '.join(checks)}" if checks else ""))
        version = FEED.add_version(store, int(work_id), note=note, variants=[{"clip_id": int(clip_id), "label": aspect}],
                                   status="review", made_by="agent")
        return {"work_id": int(work_id), "version_id": int(version["id"])}
    except Exception as exc:  # noqa: BLE001 - лента — витрина, клип уже есть
        return {"error": f"в ленту не добавлено: {str(exc)[:200]}"}


def sheet_image(amv_id: str, tag: str, max_w: int = 1600) -> tuple[bytes, dict]:
    """Лист кадров рендера (JPEG, не шире max_w) — агенту посмотреть пробу глазами."""
    import cv2

    manifest = A.load(amv_id)
    renders = manifest.get("renders") or {}
    key = tag if tag in renders else next((k for k in renders if k.split("_")[0] == tag), None)
    if key is None or not (renders[key] or {}).get("sheet"):
        raise AmvError(f"нет листа кадров {tag!r}; есть: {', '.join(renders) or 'рендеров ещё нет'}")
    img = cv2.imread(renders[key]["sheet"])
    if img is None:
        raise AmvError("лист кадров не читается")
    if img.shape[1] > max_w:
        img = cv2.resize(img, (max_w, int(img.shape[0] * max_w / img.shape[1])), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes(), {"render": key, "clip_id": renders[key].get("clip_id"), "step_sec": 0.5}


def status(amv_id: str) -> dict:
    m = A.load(amv_id)
    jobs = {k: {x: v for x, v in job_state(m, k).items() if x in ("status", "message", "error", "updated_at")}
            for k in (m.get("jobs") or {})}
    return {"amv_id": m["id"], "title": m["title"], "project_id": m["project_id"], "source_ids": m["source_ids"],
            "track_id": m["track_id"], "track": m["track_label"], "aspect": m["aspect"], "notes": m.get("notes", ""),
            "jobs": jobs, "song": m.get("song"), "library": m.get("library"),
            "plans": {k: {"t0": v["t0"], "t1": v["t1"],
                          "failed_checks": [c["name"] for c in v.get("checks", []) if c.get("ok") is False]}
                      for k, v in (m.get("plans") or {}).items()},
            "renders": m.get("renders") or {}, "feed_work_id": m.get("feed_work_id")}


__all__ = ["AmvError", "analyze_song", "cancel", "index_sources", "job_state", "library_summary", "make_plan",
           "merge_overrides", "plan_view", "render", "sheet_image", "song_summary", "sources_json", "status", "submit",
           "test_window", "to_feed", "wait"]
