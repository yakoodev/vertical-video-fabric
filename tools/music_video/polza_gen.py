# -*- coding: utf-8 -*-
"""nano-banana через Polza /media. Запускается ВНУТРИ контейнера сервиса (там ключ POLZA_API_KEY).
Runs INSIDE the app container: python gen.py balance | python gen.py OUT.jpg PROMPT_FILE [ref.jpg ...]
Env AR (9:16), RES (1K). Prints cost; never retries a paid call blindly."""
import base64, datetime, json, os, subprocess, sys, time
import httpx

B = os.environ.get("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/")
H = {"Authorization": "Bearer " + os.environ["POLZA_API_KEY"], "Content-Type": "application/json"}


def balance():
    r = httpx.get(B + "/balance", headers=H, timeout=60)
    return r.status_code, r.text[:300]


if sys.argv[1] == "balance":
    print(*balance())
    sys.exit(0)

out, pfile, refs = sys.argv[1], sys.argv[2], sys.argv[3:]
prompt = open(pfile, encoding="utf-8").read().strip()
imgs = []
for i, p in enumerate(refs):
    s = f"/tmp/music_video/ref{i}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-vf", "scale='min(1024,iw)':-2", "-q:v", "3", s], check=True)
    imgs.append({"type": "base64", "data": "data:image/jpeg;base64," + base64.b64encode(open(s, "rb").read()).decode()})
inp = {"prompt": prompt, "aspect_ratio": os.environ.get("AR", "9:16"), "image_resolution": os.environ.get("RES", "1K")}
if imgs:
    inp["images"] = imgs
body = {"model": "google/gemini-nano-banana-2.1", "input": inp}
t0 = (datetime.datetime.utcnow() - datetime.timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S")
rid, d = None, {}
try:
    r = httpx.post(B + "/media", headers=H, json=body, timeout=600)
    print("submit", r.status_code, r.text[:200], flush=True)
    if r.status_code < 400:
        d = r.json(); rid = d.get("id")
    elif r.status_code < 500:
        sys.exit(2)
except httpx.ConnectError:
    print("connect error, not sent"); sys.exit(3)
except Exception as e:
    print("submit exc", type(e).__name__)
if not rid:
    for i in range(30):
        time.sleep(10)
        h = httpx.get(B + "/history/generations", headers=H, params={"requestType": "images", "limit": 5}, timeout=60).json()
        c = [it for it in h.get("items", []) if it["createdAt"][:19] >= t0]
        if c:
            rid = c[0]["id"]; print("recovered", rid, flush=True); break
    if not rid:
        print("NO ID"); sys.exit(4)
while d.get("status") not in ("completed", "failed", "cancelled"):
    time.sleep(4)
    d = httpx.get(B + "/media/" + rid, headers=H, timeout=60).json()
print("final", d.get("status"), json.dumps(d.get("usage")))
item = d["data"]; item = item[0] if isinstance(item, list) else item
open(out, "wb").write(httpx.get(item["url"], timeout=120, follow_redirects=True).content)
print("saved", out)
