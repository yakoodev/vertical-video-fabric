"""Подбор правил «не речь» по диагностическому прогону (профиль ``diag_*``: все сегменты без порогов).

    python -m tools.asr_bench.rules --diag diag_novad__before

Каждый сегмент размечен: выдумка (набор без речи; хвост — начат после конца речи) или речь (чистая / под музыкой /
дубляж / до конца речи в хвосте). Для сегмента — no_speech, logprob, доля детектора голоса, тишина по громкости,
фантомный текст. Печатает, сколько выдумок и сколько речи (сегментов и слов) выкидывает каждое правило."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.asr_bench import gen  # noqa: E402


def segment_table(diag: str, bench: Path = gen.BENCH_DIR) -> list[dict]:
    from app.subtitles.phantoms import is_phantom_text
    from app.subtitles.speech_evidence import Evidence, load_audio

    data = json.loads((bench / "results" / f"{diag}.json").read_text("utf-8"))["results"]
    rows = []
    for it in gen.load_manifest(bench)["items"]:
        res = data.get(f"{it['set']}/{it['id']}")
        if not res:
            continue
        ev = Evidence.of(load_audio(it["path"]))
        for s in res["segments"]:
            a, b = float(s["start"]), float(s["end"])
            fake = it["set"] == "nospeech" or (it["set"] == "tail" and a >= it["speech_end"] + 0.5)
            rows.append({"set": it["set"], "id": it["id"], "start": a, "end": b, "text": s["text"], "ns": s["ns"],
                         "lp": s["lp"], "share": round(ev.share(a, b) or 0.0, 3), "silent": ev.silent(a, b),
                         "phantom": is_phantom_text(s["text"]), "fake": fake,
                         "words": len(s["text"].split()), "control": bool(it.get("control"))})
    return rows


RULES = {
    "old: ns>0.6 & lp<-0.5 | lp<-1 | phantom": lambda r: (r["ns"] > 0.6 and r["lp"] < -0.5) or r["lp"] < -1 or r["phantom"],
    "whisper default: ns>0.6 & lp<-1": lambda r: r["ns"] > 0.6 and r["lp"] < -1.0,
    "trust ns: ns>0.6": lambda r: r["ns"] > 0.6,
    "trust ns: ns>0.5": lambda r: r["ns"] > 0.5,
    "silent": lambda r: r["silent"],
    "vad share<0.2": lambda r: r["share"] < 0.2,
    "vad share<0.5": lambda r: r["share"] < 0.5,
    "ns>0.6 & share<0.3": lambda r: r["ns"] > 0.6 and r["share"] < 0.3,
    "ns>0.5 & share<0.5": lambda r: r["ns"] > 0.5 and r["share"] < 0.5,
    "phantom & (share<0.5|silent)": lambda r: r["phantom"] and (r["share"] < 0.5 or r["silent"]),
    "new: silent | phantom&share<0.5 | ns>0.6&share<0.3": lambda r: r["silent"] or (r["phantom"] and r["share"] < 0.5)
    or (r["ns"] > 0.6 and r["share"] < 0.3),
}


def evaluate(rows: list[dict]) -> list[dict]:
    out = []
    fakes = [r for r in rows if r["fake"]]
    real = [r for r in rows if not r["fake"]]
    for name, rule in RULES.items():
        hit_f = [r for r in fakes if rule(r)]
        hit_r = [r for r in real if rule(r)]
        out.append({"rule": name, "fake_dropped": f"{len(hit_f)}/{len(fakes)}",
                    "real_dropped": f"{len(hit_r)}/{len(real)}",
                    "real_words_dropped": f"{sum(r['words'] for r in hit_r)}/{sum(r['words'] for r in real)}",
                    "real_by_set": {s: sum(1 for r in hit_r if r["set"] == s) for s in ("clean", "tail", "music", "dub")},
                    "real_examples": [f"{r['set']}/{r['id']} ns={r['ns']} lp={r['lp']} share={r['share']}: {r['text'][:60]}"
                                      for r in hit_r[:6]]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", default="diag_novad__before")
    ap.add_argument("--bench", default=str(gen.BENCH_DIR))
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    rows = segment_table(a.diag, Path(a.bench))
    if a.dump:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    for e in evaluate(rows):
        print(json.dumps(e, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
