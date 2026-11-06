"""Сверка двух прогонов tools/editor_check.cjs: шаги, запросы на рендер, ошибки и кадры.

    python tools/editor_check_compare.py <папка-до> <папка-после> [порог_%]

Папка — <OUT>/<метка>, внутри desktop/ и phone/. Кадр считается другим, если заметно отличается
больше порога пикселей (по умолчанию 0.5 %). Шум двух прогонов одного кода смотрим так же: сравнить
прогон с его повтором.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
CHANNEL_DELTA = 24


def load(path: Path) -> dict:
    return json.loads((path / "result.json").read_text(encoding="utf-8"))


def diff_share(a: Path, b: Path) -> float | str:
    with Image.open(a) as ia, Image.open(b) as ib:
        if ia.size != ib.size:
            return f"размер {ia.size} и {ib.size}"
        pa = np.asarray(ia.convert("RGB"), dtype=np.int16)
        pb = np.asarray(ib.convert("RGB"), dtype=np.int16)
    changed = (np.abs(pa - pb).max(axis=2) > CHANNEL_DELTA).sum()
    return float(changed) * 100 / (pa.shape[0] * pa.shape[1])


def compare_steps(before: dict, after: dict) -> list[str]:
    out: list[str] = []
    old = {s["name"]: s for s in before["steps"]}
    new = {s["name"]: s for s in after["steps"]}
    for name in old.keys() | new.keys():
        if name not in old or name not in new:
            out.append(f"шаг только в одном прогоне: {name}")
            continue
        a, b = old[name], new[name]
        if a.get("error") != b.get("error"):
            out.append(f"{name}: ошибка «{a.get('error')}» → «{b.get('error')}»")
        ca = {c["name"]: c for c in a["checks"]}
        cb = {c["name"]: c for c in b["checks"]}
        for key in ca.keys() | cb.keys():
            va, vb = ca.get(key), cb.get(key)
            if va != vb:
                out.append(f"{name} / {key}: {json.dumps(va, ensure_ascii=False)} → {json.dumps(vb, ensure_ascii=False)}")
    if before.get("payloads") != after.get("payloads"):
        for key in (before.get("payloads") or {}).keys() | (after.get("payloads") or {}).keys():
            if before["payloads"].get(key) != after["payloads"].get(key):
                out.append(f"запрос на рендер {key}: {before['payloads'].get(key)} → {after['payloads'].get(key)}")
    for field in ("errors", "failedApi"):
        if sorted(before.get(field) or []) != sorted(after.get(field) or []):
            out.append(f"{field}: {before.get(field)} → {after.get(field)}")
    return sorted(out)


def main() -> int:
    before_dir, after_dir = Path(sys.argv[1]), Path(sys.argv[2])
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    total_bad = 0
    report: dict = {}
    for width in ("desktop", "phone"):
        a, b = before_dir / width, after_dir / width
        if not (a / "result.json").exists() or not (b / "result.json").exists():
            print(f"{width}: нет результатов")
            continue
        before, after = load(a), load(b)
        print(f"--- {width}: шагов {after['summary']['steps']}, упало {len(after['summary']['failed'])} (было {len(before['summary']['failed'])}), "
              f"пропущено {len(after['summary']['skipped'])}, ошибок страницы {after['summary']['pageErrors']}, ошибок API {after['summary']['failedApi']}")
        for name in after["summary"]["failed"]:
            print(f"  упал: {name}")
        steps = compare_steps(before, after)
        for line in steps:
            print(f"  шаг: {line}")
        names_a = {p.name for p in a.glob("*.png")}
        names_b = {p.name for p in b.glob("*.png")}
        shots: dict[str, float | str] = {}
        for name in sorted(names_a | names_b):
            if name not in names_a or name not in names_b:
                shots[name] = "только в одном прогоне"
                print(f"  кадр: {name}: только в одном прогоне")
                continue
            d = diff_share(a / name, b / name)
            shots[name] = d
            if isinstance(d, str) or d > threshold:
                print(f"  кадр: {name}: {d if isinstance(d, str) else f'{d:.2f} %'}")
        same = sum(1 for v in shots.values() if isinstance(v, float) and v <= threshold)
        worst = max((v for v in shots.values() if isinstance(v, float)), default=0.0)
        print(f"  кадров совпало {same} из {len(shots)}, самое большое отличие {worst:.3f} %")
        total_bad += len(steps) + (len(shots) - same) + len(after["summary"]["failed"])
        report[width] = {"steps": steps, "shots": shots}
    (after_dir / f"compare_{before_dir.name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("расхождений" if total_bad else "расхождений нет", total_bad or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
