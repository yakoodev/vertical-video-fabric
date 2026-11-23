"""Хост-воркер 3D-рендера: берёт у сервиса задания 3D-серий и «клипа на песню» (kind ``music_video``,
tools/music_video/remote.py), рендерит Blender'ом на этом компьютере, отдаёт mp4. Задание 3D-серии с
``render_backend: threejs``: Blender только ставит позы, кадры рисует three.js в headless Chromium на GPU (нужен node).

В Docker сервиса нет GPU и Blender — поэтому рендер идёт здесь (Windows-хост, RTX). Сервис ставит задание
(«Истории» → 3D-серия → Blender), воркер забирает его, шлёт пульс с процентом (в UI: «рендер на компьютере… N%»),
кадры кладёт в свой кэш (упал/выключили — следующий запуск дорисует), склеивает и загружает mp4.

    python tools/render_worker.py --server http://localhost:8088 [--token-file %TEMP%\\vvtok.txt]
        [--work D:/vvf-offload/render3d] [--name pc] [--poll 10] [--once]

Токен — тот же Bearer, что у MCP (``--token``/``--token-file``/``VVF_TOKEN``); в репозиторий не кладём.
Нужны: Blender 4.5 LTS (``BLENDER_BIN`` или portable в D:/vvf-offload/tools/blender), паки ``kaykit-adventurers``,
``kaykit-animations`` (``tools/fetch_packs.py``), ffmpeg.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import series3d_blender as SB  # noqa: E402

DEFAULT_TOKEN_FILE = Path(os.environ.get("TEMP", "/tmp")) / "vvtok.txt"
# Какие задания берёт воркер: 3D-серии и «клип на песню» (tools/music_video/remote.py).
KINDS = ("series3d", "music_video")


class Api:
    """Тонкий клиент ручек ``/api/render3d/*`` (httpx)."""

    def __init__(self, server: str, token: str, timeout: float = 60.0) -> None:
        import httpx

        self.base = server.rstrip("/")
        self.http = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}"} if token else {})

    def _post(self, path: str, **kw: Any) -> dict:
        r = self.http.post(self.base + path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def claim(self, worker: str, info: dict) -> dict | None:
        return self._post("/api/render3d/claim", json={"worker": worker, "info": info, "kinds": list(KINDS)}).get("job")

    def download(self, path: str, dest: Path) -> None:
        """Файл сервиса (архив задания «клип на песню») — на диск, частями."""
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self.http.stream("GET", self.base + path, timeout=600) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: HTTP {r.status_code}")
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)

    def heartbeat(self, job_id: int, worker: str, progress: float | None, note: str = "") -> dict:
        return self._post(f"/api/render3d/jobs/{job_id}/heartbeat",
                          json={"worker": worker, "progress": progress, "note": note})

    def complete(self, job_id: int, worker: str, mp4: Path, result: dict) -> dict:
        with mp4.open("rb") as fh:
            return self._post(f"/api/render3d/jobs/{job_id}/complete",
                              files={"file": (mp4.name, fh, "video/mp4")},
                              data={"worker": worker, "result": json.dumps(result, ensure_ascii=False)})

    def fail(self, job_id: int, worker: str, error: str, retry: bool = False) -> dict:
        return self._post(f"/api/render3d/jobs/{job_id}/fail", json={"worker": worker, "error": error, "retry": retry})


class Pulse:
    """Пульс в фоне (и пока Blender строит сцену): последний процент, не чаще раза в ``every`` с; задание отменили —
    ``stop`` = True."""

    def __init__(self, api: Api, job_id: int, worker: str, every: float = 8.0) -> None:
        self.api, self.job_id, self.worker, self.every = api, job_id, worker, every
        self.progress, self.note = 0.0, "сцена Blender"
        self.stop = False
        self._sent = 0.0
        self._done = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def update(self, progress: float, note: str) -> None:
        self.progress, self.note = progress, note
        if time.time() - self._sent >= self.every:
            self._send()

    def _send(self) -> None:
        self._sent = time.time()
        try:
            r = self.api.heartbeat(self.job_id, self.worker, self.progress, self.note)
            if r.get("stop"):
                self.stop = True
        except Exception as exc:  # noqa: BLE001 - сеть мигнула — следующий пульс
            print(f"пульс не ушёл: {exc}", flush=True)

    def _loop(self) -> None:
        while not self._done.wait(self.every):
            if time.time() - self._sent >= self.every:
                self._send()

    def close(self) -> None:
        self._done.set()


def run_music_video(api: Api, job: dict, worker: str, work_root: Path, blender: str) -> dict:
    """«Клип на песню»: архив задания → кадры Blender → mp4 (tools/music_video/remote.py)."""
    sys.path.insert(0, str(ROOT / "tools" / "music_video"))
    import remote as MV  # noqa: E402 - модуль инструмента клипа на песню

    jid = int(job["id"])
    payload = job["payload"]
    out = work_root / f"mv-{payload.get('mv_job')}-job-{jid}"
    pulse = Pulse(api, jid, worker)
    t0 = time.time()
    try:
        print(f"задание {jid}: {job.get('title') or ''} — {payload.get('nf')} кадров", flush=True)
        video = MV.render_bundle(api.download, payload, out, blender,
                                 progress=lambda p, note: (pulse.update(p, note), print(f"  {p * 100:5.1f}% {note}",
                                                                                          flush=True)),
                                 should_stop=lambda: pulse.stop)
        pulse.update(1.0, "загрузка mp4")
        result = {"frames": payload.get("nf"), "worker": worker, "wall_sec": round(time.time() - t0, 1)}
        api.complete(jid, worker, video, result)
        print(f"задание {jid} готово за {result['wall_sec']} с", flush=True)
        return result
    except Exception as exc:  # noqa: BLE001 - любое падение — в сервис, воркер живёт дальше
        err = f"{type(exc).__name__}: {exc}"
        print(f"задание {jid} упало: {err}\n{traceback.format_exc()[-1500:]}", flush=True)
        if not pulse.stop:
            try:
                api.fail(jid, worker, err[:1800], retry=isinstance(exc, OSError))
            except Exception as exc2:  # noqa: BLE001
                print(f"не сообщил об ошибке: {exc2}", flush=True)
        return {"error": err}
    finally:
        pulse.close()


def localize_photos(api: Any, payload: dict, cache: Path) -> dict:
    """Фото переписки (``photo_assets``: ключ → id ассета) — файлы сервиса, здесь их нет: скачать в ``cache/photos``
    (имя по id — хэш кадров между запусками тот же) и подставить путь в ``photos``. Файл уже есть — как есть."""
    ids = payload.get("photo_assets") or {}
    if not ids:
        return payload
    photos = dict(payload.get("photos") or {})
    folder = cache / "photos"
    folder.mkdir(parents=True, exist_ok=True)
    for key, aid in ids.items():
        cur = photos.get(key)
        if cur and Path(cur).is_file():
            continue
        dest = folder / f"asset-{int(aid)}{Path(str(cur or '')).suffix or '.png'}"
        if not dest.is_file():
            api.download(f"/media/montage-assets/{int(aid)}", dest)
        photos[key] = str(dest)
    payload["photos"] = photos
    return payload


def run_job(api: Api, job: dict, worker: str, work_root: Path, cache: Path) -> dict:
    if job.get("kind") == "music_video":
        return run_music_video(api, job, worker, work_root, SB.find_blender() or "")
    jid = int(job["id"])
    payload = job["payload"]
    work = work_root / f"job-{jid}"
    out = work / "series.mp4"
    pulse = Pulse(api, jid, worker)
    t0 = time.time()
    try:
        print(f"задание {jid}: {job.get('title') or ''} — {len(payload.get('shots') or [])} планов, "
              f"{payload.get('duration')} с", flush=True)
        localize_photos(api, payload, cache)
        info = SB.render_local(payload, out, work=work, cache=cache,
                               progress=lambda p, note: (pulse.update(p, note), print(f"  {p * 100:5.1f}% {note}", flush=True)),
                               should_stop=lambda: pulse.stop)
        pulse.update(1.0, "загрузка mp4")
        result = {"seconds": info.get("seconds"), "frames": info.get("frames"), "rendered": info.get("rendered"),
                  "cached": info.get("cached"), "chunks": info.get("chunks"), "worker": worker, "backend": info.get("backend"),
                  "wall_sec": round(time.time() - t0, 1)}
        api.complete(jid, worker, out, result)
        print(f"задание {jid} готово за {result['wall_sec']} с (рендер {info.get('rendered')} кадров, "
              f"из кэша {info.get('cached')})", flush=True)
        return result
    except Exception as exc:  # noqa: BLE001 - любое падение — в сервис, воркер живёт дальше
        err = f"{type(exc).__name__}: {exc}"
        print(f"задание {jid} упало: {err}\n{traceback.format_exc()[-1500:]}", flush=True)
        if not pulse.stop:
            try:
                api.fail(jid, worker, err[:1800], retry=isinstance(exc, (OSError, SB.RenderError)) and "отменено" not in err)
            except Exception as exc2:  # noqa: BLE001
                print(f"не сообщил об ошибке: {exc2}", flush=True)
        return {"error": err}
    finally:
        pulse.close()


def _token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token.strip()
    if os.environ.get("VVF_TOKEN"):
        return os.environ["VVF_TOKEN"].strip()
    path = Path(args.token_file) if args.token_file else DEFAULT_TOKEN_FILE
    return path.read_text("utf-8").strip() if path.is_file() else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Хост-воркер 3D-рендера (Blender) для сервиса")
    ap.add_argument("--server", default=os.environ.get("VVF_SERVER", "http://localhost:8088"))
    ap.add_argument("--token")
    ap.add_argument("--token-file")
    ap.add_argument("--name", default=os.environ.get("RENDER_WORKER_NAME") or socket.gethostname())
    ap.add_argument("--work", default=os.environ.get("RENDER_WORKER_DIR", "D:/vvf-offload/render3d"))
    ap.add_argument("--poll", type=float, default=10.0)
    ap.add_argument("--once", action="store_true", help="одно задание (или выйти, если очереди нет)")
    args = ap.parse_args(argv)
    blender = SB.find_blender()
    if not blender:
        print("нет Blender: задай BLENDER_BIN (portable 4.5 LTS с blender.org)", file=sys.stderr)
        return 2
    api = Api(args.server, _token(args))
    work = Path(args.work)
    cache = work / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    info = {"blender": blender, "host": platform.node(), "python": platform.python_version(), "pid": os.getpid()}
    print(f"воркер {args.name}: {args.server}, Blender {blender}, кэш {cache}", flush=True)
    while True:
        try:
            job = api.claim(args.name, info)
        except Exception as exc:  # noqa: BLE001 - сервис недоступен — подождать
            print(f"сервис недоступен: {exc}", flush=True)
            job = None
            if args.once:
                return 1
        if job:
            run_job(api, job, args.name, work, cache)
            if args.once:
                return 0
            continue
        if args.once:
            print("очередь пуста", flush=True)
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
