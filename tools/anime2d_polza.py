"""Платная генерация картинок пресета через Polza (nano-banana) — со сметой, потолком и журналом трат.

Всё платное идёт ВНУТРИ контейнера приложения (там ключ): сюда копируется
tools/anime2d_polza_job.py, референсы и задание; результат копируется обратно в ``src``.

* ``--dry-run`` — только смета: какие картинки, почём, итог; ничего не тратится;
* ``max_rub`` — потолок: если смета больше, не начинаем; во время прогона каждая следующая картинка
  проверяется против уже потраченного;
* после любой ошибки неизвестного исхода (таймаут без id и пустая история) прогон ОСТАНАВЛИВАЕТСЯ,
  повторов нет — сначала баланс и /history руками;
* каждая трата — строка в журнале ``paths.spend_log``; баланс до и после печатается и пишется туда же.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path

from tools import anime2d_preset as P

ROOT = Path(__file__).resolve().parent.parent
JOB = ROOT / "tools" / "anime2d_polza_job.py"
WORKDIR = "/tmp/probe/anime2d"


class SpendError(RuntimeError):
    """Остановка платного прогона (потолок, неизвестный исход вызова)."""


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"  # Git Bash иначе превращает /tmp/... в путь Windows
    return env


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=_env(), check=check)


def _fwd(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _job(container: str, *args: str) -> dict:
    proc = _docker("exec", container, "python", f"{WORKDIR}/job.py", *args, check=False)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            out = json.loads(line[7:])
            out["code"] = proc.returncode
            return out
    return {"code": proc.returncode, "status": "no_result", "error": (proc.stderr or proc.stdout)[-400:]}


def prepare(container: str) -> None:
    _docker("exec", container, "mkdir", "-p", WORKDIR)
    _docker("cp", _fwd(JOB), f"{container}:{WORKDIR}/job.py")


def balance(container: str) -> float | None:
    r = _job(container, "balance")
    try:
        return float(r["amount"])
    except (KeyError, TypeError, ValueError):
        return None


def log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M} {line}\n")


def print_plan(preset: dict, items: list[dict]) -> float:
    total = sum(i["rub"] for i in items)
    if not items:
        print("смета: все картинки уже есть — 0 ₽")
        return 0.0
    print(f"смета ({preset['generation']['model']}):")
    for i in items:
        im = i["image"]
        print(f"  {im['id']:<14} {im['res']} {im['ar']:<5} {i['rub']:>5.1f} ₽  → {im['file']}")
    print(f"  итого: {total:.1f} ₽ за {len(items)} шт. (переделки не учтены — закладывайте запас)")
    return total


def generate(preset: dict, src: Path, max_rub: float, dry_run: bool = True, only: list[str] | None = None,
             force: bool = False) -> float:
    """Сгенерировать недостающие картинки. Возвращает потраченное (по ответам Polza), в dry-run — смету."""
    items = P.plan(preset, src, only, force)
    total = print_plan(preset, items)
    if dry_run or not items:
        return total
    if total > max_rub:
        raise SpendError(f"смета {total:.1f} ₽ больше потолка --max-rub {max_rub:.1f} ₽ — ничего не тратим")
    gen = preset["generation"]
    container = gen["container"]
    spend_log = Path(preset.get("paths", {}).get("spend_log") or src.parent / "ai_spend.log")
    prepare(container)
    before = balance(container)
    print(f"баланс до: {before}")
    log(spend_log, f"[{preset['id']}] start, balance {before}, estimate {total:.1f} RUB, cap {max_rub:.1f}")
    files = {iid: im["file"] for iid, im in P.images(preset).items()}
    spent = 0.0
    for item in items:
        im = item["image"]
        if spent + item["rub"] > max_rub:
            raise SpendError(f"следующая картинка {im['id']} выйдет за потолок ({spent:.1f} + {item['rub']:.1f})")
        refs = []
        for n, ref in enumerate(im.get("refs", [])):
            local = src / files[ref]
            if not local.is_file():
                raise SpendError(f"{im['id']}: нет референса {local}")
            dst = f"{WORKDIR}/{im['id']}_r{n}.png"
            _docker("cp", _fwd(local), f"{container}:{dst}")
            refs.append(dst)
        out = f"{WORKDIR}/{im['id']}.png"
        job = {"model": gen["model"], "prompt": P.prompt_text(preset, im), "ar": im["ar"], "res": im["res"],
               "refs": refs, "out": out}
        jpath = src.parent / f".job_{im['id']}.json"
        jpath.write_text(json.dumps(job, ensure_ascii=False), "utf-8")
        _docker("cp", _fwd(jpath), f"{container}:{WORKDIR}/job.json")
        jpath.unlink()
        print(f"→ {im['id']} ({im['res']}, ~{item['rub']:.1f} ₽)...", flush=True)
        r = _job(container, "image", f"{WORKDIR}/job.json")
        if r.get("status") != "completed":
            log(spend_log, f"[{preset['id']}] {im['id']} FAILED {r}")
            raise SpendError(f"{im['id']}: {r.get('status')} — {r.get('error')}. НЕ повторять вслепую: "
                             f"проверьте /balance и /history/generations (см. docs/workflows/ai-anime-2d.md)")
        _docker("cp", f"{container}:{out}", _fwd(src / im["file"]))
        cost = float(r.get("cost_rub") or item["rub"])
        spent += cost
        log(spend_log, f"[{preset['id']}] {im['id']} {im['file']} {im['res']} {im['ar']} {r.get('id')} {cost} RUB")
        print(f"  готово: {im['file']}, {cost} ₽ (всего {spent:.1f})")
    after = balance(container)
    print(f"баланс после: {after}; потрачено {spent:.1f} ₽")
    log(spend_log, f"[{preset['id']}] done, spent {spent:.1f} RUB, balance {before} -> {after}")
    return spent
