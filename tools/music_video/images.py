# -*- coding: utf-8 -*-
"""Шаг images: картинки сцен через nano-banana (Polza /media) в контейнере сервиса.

Ключ Polza есть только в контейнере, поэтому скрипт polza_gen.py копируется туда и запускается docker exec.
Платный шаг: нужен --max-rub; цена одной картинки ≈ PRICE_RUB, реальный расход берётся по балансу до/после
и дописывается в OUT/work/spend.log. Уже скачанные картинки не перегенерируются (кроме --force id,id)."""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICE_RUB = 3.0
CDIR = "/tmp/music_video"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=check, env=ENV)


def balance(container: str) -> float | None:
    for _ in range(4):
        p = _docker("exec", container, "python", f"{CDIR}/polza_gen.py", "balance", check=False)
        if p.returncode == 0 and p.stdout.startswith("200"):
            return float(json.loads(p.stdout.split(" ", 1)[1])["available"])
        time.sleep(5)
    return None


def style_prompt(plan: dict, spec: dict) -> str:
    st = plan.get("image_style", {})
    kind = spec.get("kind", "plate")
    tail = st.get("cutout" if kind == "cutout" else "plate", "")
    if kind == "cutout":
        key = spec.get("key", "green")
        bg = {"green": "perfectly flat solid chroma-key green background (#00FF00)",
              "magenta": "perfectly flat solid magenta background (#FF00FF)",
              "white": "plain flat white background"}[key]
        tail = (tail + f" Isolated on a {bg}, evenly lit, no shadow on the background, no ground, "
                "clean bold outline around the whole silhouette, nothing touches the frame edges.").strip()
    ids = spec.get("chars", [])
    cards = " ".join(f"{c.upper()} (keep exactly this look in every image): {plan['characters'][c]}" for c in ids
                     if c in plan.get("characters", {}))
    if spec.get("refs"):
        cards = ("Use the attached reference image(s) as the model sheet: the same character design, face, "
                 "proportions, colours and clothes. " + cards).strip()
    return f"{spec['prompt'].strip()} {cards} {st.get('common', '').strip()} {tail}".strip()


def main(plan: dict, out: Path, max_rub: float | None, container: str, only: list[str] | None = None,
         force: list[str] | None = None, dry_run: bool = False) -> None:
    work = out / "work"; (work / "img").mkdir(parents=True, exist_ok=True); (work / "prompts").mkdir(exist_ok=True)
    todo = []
    for iid, spec in plan["images"].items():
        if only and iid not in only:
            continue
        dst = work / "img" / f"{iid}.jpg"
        if dst.exists() and not (force and iid in force):
            continue
        pr = style_prompt(plan, spec)
        (work / "prompts" / f"{iid}.txt").write_text(pr, encoding="utf-8")
        todo.append((iid, spec, dst))
    est = len(todo) * PRICE_RUB
    print(f"  картинок к генерации: {len(todo)} ≈ {est:.0f} ₽", [t[0] for t in todo])
    if not todo or dry_run:
        return
    if max_rub is None or est > max_rub:
        raise SystemExit(f"  смета {est:.0f} ₽ больше --max-rub {max_rub}: ничего не отправлено")
    _docker("exec", container, "mkdir", "-p", CDIR)
    _docker("cp", str(HERE / "polza_gen.py"), f"{container}:{CDIR}/polza_gen.py")
    b0 = balance(container)
    done = []
    for iid, spec, dst in todo:
        _docker("cp", str(work / "prompts" / f"{iid}.txt"), f"{container}:{CDIR}/{iid}.txt")
        refs = []
        for r in spec.get("refs", []):
            rp = work / "img" / f"{r}.jpg"
            _docker("cp", str(rp), f"{container}:{CDIR}/ref_{r}.jpg")
            refs.append(f"{CDIR}/ref_{r}.jpg")
        ar = spec.get("ar", "9:16")
        for attempt in range(3):
            p = _docker("exec", "-e", f"AR={ar}", "-e", f"RES={spec.get('res', '1K')}", container, "python",
                        f"{CDIR}/polza_gen.py", f"{CDIR}/{iid}.jpg", f"{CDIR}/{iid}.txt", *refs, check=False)
            print("   ", iid, p.stdout.strip().splitlines()[-1:] if p.stdout else p.stderr[-300:], flush=True)
            if p.returncode == 3 or ("ConnectError" in p.stderr and "submit" not in p.stdout):
                time.sleep(8); continue          # запрос не ушёл — повтор безопасен
            break
        if p.returncode == 0:
            _docker("cp", f"{container}:{CDIR}/{iid}.jpg", str(dst))
            done.append(iid)
        else:
            print("    ! не получилось:", iid, p.stderr[-400:])
    b1 = balance(container)
    spent = (b0 - b1) if (b0 is not None and b1 is not None) else len(done) * PRICE_RUB
    line = (f"{datetime.datetime.now():%Y-%m-%d %H:%M} | {out.name} | nano-banana 1K x{len(done)} ({', '.join(done)}) | "
            f"{spent:.2f} RUB | Polza balance {b0} -> {b1}\n")
    with open(work / "spend.log", "a", encoding="utf-8") as fh:
        fh.write(line)
    print("  " + line.strip())
