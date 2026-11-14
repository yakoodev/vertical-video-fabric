"""Замер подбора видеомема к реплике: по словам (как было) против смысла (app/text_embed + app/meme_semantic).

Разметка — tests/fixtures/meme_semantic_eval.json (выбор монтажёра-человека из раскадровок пилотов 1–2), библиотека —
каталог D:/vvf-offload/memes/collect/catalog.json (222 мема). Для каждой реплики все мемы ранжируются, считается
top-1 / top-5 (мем монтажёра) и top-1 «годится» (мем монтажёра или его запасной).

    set DATA_DIR=D:\\vvf-offload\\tmp\\semeval   (кэш векторов — не в данные сервиса)
    set EMBED_MODEL_DIR=D:\\vvf-offload\\models\\embed-models
    python tools/meme_semantic_eval.py [--model e5-small|minilm] [--show]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CATALOG = Path("D:/vvf-offload/memes/collect/catalog.json")
FIXTURE = ROOT / "tests" / "fixtures" / "meme_semantic_eval.json"


def library(path: Path) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return [{"meme_id": int(m["id"]), "id": int(m["id"]), "title": m.get("label") or "", "tags": m.get("tags") or [],
             "description": m.get("description") or "", "when_to_use": m.get("when_to_use") or "",
             "transcript": m.get("transcript") or "", "duration_sec": float(m.get("duration") or 2.0)} for m in items]


def cand_for(item: dict):
    from app.meme_edit_fights import Fight
    from app.meme_edit_memes import Candidate

    return Candidate(Fight(1, 0.0, 30.0), 10.0, item["kind"], item["line"], 1.0, 8.0, False)


def _order(points: list[float], lib: list[dict]) -> list[int]:
    return [lib[i]["meme_id"] for i in sorted(range(len(lib)), key=lambda i: (-points[i], lib[i]["meme_id"]))]


def rank_lexical(item: dict, lib: list[dict], with_context: bool) -> list[int]:
    from app.meme_edit_memes import meme_fit

    cand = cand_for(item)
    if with_context:
        cand.text = " ".join([*item.get("context", []), item["line"]])
    return _order([meme_fit(m, cand)[0] for m in lib], lib)


def rank_semantic_only(item: dict, lib: list[dict], bank) -> list[int]:
    from app.meme_semantic import line_scores

    z = line_scores(item["line"], bank)
    return _order([0.0] * len(lib) if z is None else [float(x) for x in z], lib)


def rank_combined(item: dict, lib: list[dict], bank) -> list[int]:
    from app.meme_edit_memes import meme_fit, semantic_z

    cand = cand_for(item)
    z = semantic_z(cand, bank)
    return _order([meme_fit(m, cand, None if z is None else float(z[i]))[0] for i, m in enumerate(lib)], lib)


def report(name: str, ranks: dict[str, list[int]], items: list[dict], show: bool) -> dict:
    top1 = top5 = top10 = ok1 = 0
    mrr = 0.0
    rows = []
    for it in items:
        order = ranks[it["id"]]
        pos = order.index(it["expect"]) + 1 if it["expect"] in order else 999
        top1 += pos == 1
        top5 += pos <= 5
        top10 += pos <= 10
        mrr += 1.0 / pos
        ok1 += order[0] in [it["expect"], *it.get("ok", [])]
        rows.append((it["id"], it["line"][:38], it["expect"], pos, order[:5]))
    n = len(items)
    median = sorted(r[3] for r in rows)[n // 2]
    print(f"{name:32s} top-1 {top1:2d}/{n}  top-5 {top5:2d}/{n}  top-10 {top10:2d}/{n}  top-1 годится {ok1:2d}/{n}  "
          f"MRR {mrr / n:.3f}  медиана места {median}")
    if show:
        for r in rows:
            print(f"   {r[0]:18s} {r[1]:40s} ждём {r[2]:3d}  место {r[3]:3d}  топ {r[4]}")
    return {"top1": top1, "top5": top5, "top10": top10, "ok1": ok1, "n": n, "mrr": mrr / n}


def assignment(items: list[dict], lib: list[dict], semantic: bool) -> None:
    """Как в сборке (app/meme_edit_memes.assign_memes): каждая реплика с сетапом — свой бой, мемов — по одному на
    бой, без повторов. Сколько мемов монтажёра встали ровно после его реплики и сколько ушли к чужой реплике (как
    Малышева к «бочка кончилась» вместо «Альцгеймер»)."""
    from app import meme_edit_memes as M
    from app import meme_semantic as MS
    from app.meme_edit_fights import Fight

    fights = []
    for k, it in enumerate(items):
        texts = [*it.get("context", []), it["line"]]
        lines = [{"start": 2.0 + 3 * i, "end": 4.0 + 3 * i, "text": t, "words": []} for i, t in enumerate(texts)]
        events = [{"t": lines[-1]["start"] - 0.5, "kind": it["kind"], "score": 0.9}] \
            if it["kind"] in M.KIND_TAGS and it["kind"] not in ("reaction", "none") else []
        fights.append(Fight(k + 1, 0.0, lines[-1]["end"] + 1.0, pieces=[[0.0, lines[-1]["end"] + 1.0]],
                            events=events, lines=lines))
    real_bank = MS.bank
    if not semantic:
        MS.bank = lambda _memes: None  # type: ignore[assignment]
    try:
        per_fight, M.MAX_PER_FIGHT = M.MAX_PER_FIGHT, 1
        picks, _notes = M.assign_memes(fights, lib, float(len(items)), every_sec=1.0)
    finally:
        MS.bank = real_bank  # type: ignore[assignment]
        M.MAX_PER_FIGHT = per_fight
    exact = good = away = 0
    where = {}
    for p in picks:
        where[int(p.meme["meme_id"])] = (p.cand.fight.source_id, p.cand.text)
    for k, it in enumerate(items):
        mine = next((p for p in picks if p.cand.fight.source_id == k + 1 and p.cand.text == it["line"]), None)
        exact += bool(mine and int(mine.meme["meme_id"]) == it["expect"])
        good += bool(mine and int(mine.meme["meme_id"]) in [it["expect"], *it.get("ok", [])])
        placed = where.get(it["expect"])
        away += bool(placed and placed != (k + 1, it["line"]))
    print(f"сборка {'со смыслом' if semantic else 'по словам':12s}: мем монтажёра после его реплики {exact}/{len(items)}, "
          f"годный мем {good}/{len(items)}, мем монтажёра ушёл к чужой реплике {away}, мемов поставлено {len(picks)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="e5-small | minilm (EMBED_MODEL)")
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--show", action="store_true", help="место мема монтажёра по каждой реплике")
    args = ap.parse_args()
    if args.model:
        os.environ["EMBED_MODEL"] = args.model
    from app import meme_semantic as MS
    from app import text_embed as TE

    lib = library(Path(args.catalog))
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]
    report("по словам (реплика)", {it["id"]: rank_lexical(it, lib, False) for it in items}, items, args.show)
    report("по словам (реплика + сетап)", {it["id"]: rank_lexical(it, lib, True) for it in items}, items, args.show)
    t0 = time.perf_counter()
    bank = MS.bank(lib)
    if bank is None:
        print("модель не загрузилась — смысловой подбор не замерить")
        return
    t_lib = time.perf_counter() - t0
    report(f"смысл ({TE.model_name()}) один", {it["id"]: rank_semantic_only(it, lib, bank) for it in items},
           items, args.show)
    report(f"смысл + слова ({TE.model_name()})", {it["id"]: rank_combined(it, lib, bank) for it in items},
           items, args.show)
    assignment(items, lib, semantic=False)
    assignment(items, lib, semantic=True)
    t0 = time.perf_counter()
    for it in items:
        MS.line_scores(it["line"], bank)
    per_line = (time.perf_counter() - t0) / len(items) * 1000
    lines = [it["line"] for it in items] * 3
    t0 = time.perf_counter()
    for ln in lines:
        TE.embed([ln + " ."])  # мимо кэша слов
    per = (time.perf_counter() - t0) / len(lines) * 1000
    print(f"скорость: библиотека {len(lib)} мемов {t_lib:.1f} с (первый раз — без кэша); фраза {per:.1f} мс; "
          f"реплика против всей библиотеки (фраза + эхо слов) {per_line:.1f} мс")


if __name__ == "__main__":
    main()
