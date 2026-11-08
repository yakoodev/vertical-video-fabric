"""ЗАПУСКАЕТСЯ ВНУТРИ контейнера приложения (там ключ Polza в окружении; ключ не печатается).

    python anime2d_polza_job.py balance
    python anime2d_polza_job.py image /tmp/probe/anime2d/job.json

job.json: {"model", "prompt", "ar", "res", "refs": [пути в контейнере], "out": путь в контейнере}.
Печатает одну строку ``RESULT {...}``: status, id, cost_rub (или error).

Правила безопасности (см. docs/workflows/ai-anime-2d.md):
* после таймаута / 5xx НЕ повторяем запрос — ищем задачу в /history/generations по времени и забираем
  результат по id; не нашли — код 4, хозяин скрипта останавливается и человек смотрит баланс;
* 4xx — запрос не принят (код 2), ConnectError до отправки — не списано (код 3).
"""

import base64
import datetime
import json
import os
import subprocess
import sys
import time

import httpx

BASE = os.environ.get("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/")


def _headers() -> dict:
    return {"Authorization": "Bearer " + os.environ["POLZA_API_KEY"], "Content-Type": "application/json"}


def result(code: int, **kw) -> None:
    print("RESULT " + json.dumps(kw, ensure_ascii=False), flush=True)
    sys.exit(code)


def balance() -> None:
    r = httpx.get(BASE + "/balance", headers=_headers(), timeout=60)
    d = r.json()
    result(0, status="ok", amount=d.get("amount"), reserved=d.get("reservedAmount"))


def image(job_path: str) -> None:
    job = json.load(open(job_path, encoding="utf-8"))
    imgs = []
    for i, p in enumerate(job.get("refs", [])):
        s = os.path.join(os.path.dirname(job_path), f"ref{i}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-vf", "scale='min(1024,iw)':-2", "-q:v", "3", s],
                       check=True)
        imgs.append({"type": "base64", "data": "data:image/jpeg;base64," + base64.b64encode(open(s, "rb").read()).decode()})
    body = {"model": job["model"], "input": {"prompt": job["prompt"], "aspect_ratio": job["ar"],
                                             "image_resolution": job["res"], "images": imgs}}
    t0 = (datetime.datetime.utcnow() - datetime.timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S")
    rid, d = None, {}
    try:
        r = httpx.post(BASE + "/media", headers=_headers(), json=body, timeout=600)
        print("submit", r.status_code, flush=True)
        if r.status_code < 400:
            d = r.json()
            rid = d.get("id")
        elif r.status_code < 500:
            result(2, status="rejected", error=r.text[:300])
    except httpx.ConnectError:
        result(3, status="not_sent", error="connect error before send — not charged")
    except Exception as exc:  # таймаут и т. п.: задача могла уйти и списаться — ищем в истории
        print("submit exc", type(exc).__name__, flush=True)
    if not rid:
        for _ in range(30):
            time.sleep(10)
            h = httpx.get(BASE + "/history/generations", headers=_headers(),
                          params={"requestType": "images", "limit": 5}, timeout=60).json()
            fresh = [it for it in h.get("items", []) if it["createdAt"][:19] >= t0]
            if fresh:
                rid = fresh[0]["id"]
                print("recovered", rid, flush=True)
                break
        if not rid:
            result(4, status="unknown", error="no id and nothing in history — check /balance, do NOT retry")
    while d.get("status") not in ("completed", "failed", "cancelled"):
        time.sleep(5)
        d = httpx.get(BASE + "/media/" + rid, headers=_headers(), timeout=60).json()
    if d.get("status") != "completed":
        result(5, status=d.get("status"), id=rid, error=str(d.get("error"))[:300])
    item = d["data"][0] if isinstance(d["data"], list) else d["data"]
    open(job["out"], "wb").write(httpx.get(item["url"], timeout=120, follow_redirects=True).content)
    usage = d.get("usage") or {}
    result(0, status="completed", id=rid, cost_rub=usage.get("cost_rub"))


if __name__ == "__main__":
    if sys.argv[1] == "balance":
        balance()
    elif sys.argv[1] == "image":
        image(sys.argv[2])
    else:
        result(1, status="usage", error="balance | image JOB.json")
