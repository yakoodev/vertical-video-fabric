"""CLI стенда: build | run --profile X [--tag before] | report [--tag before].

Модель: ``--device cpu --compute int8`` — как в контейнере сервиса; ``--device cuda --compute int8_float16`` —
быстро (DLL cuBLAS/cuDNN берутся из ``ASR_BENCH_CUDA_DLLS``, по умолчанию torch/lib окружения голоса).
Модели — только из локального кэша huggingface (ничего не качается)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# «До» правок: ASR_BENCH_APP_ROOT — папка со старой копией app/ (git archive <коммит> app), она берётся первой.
if os.getenv("ASR_BENCH_APP_ROOT"):
    sys.path.insert(0, os.environ["ASR_BENCH_APP_ROOT"])

from tools.asr_bench import gen, metrics  # noqa: E402

CUDA_DLLS = os.getenv("ASR_BENCH_CUDA_DLLS", "D:/vvf-offload/voice/envs/ce/Lib/site-packages/torch/lib")
HF_CACHE = os.getenv("ASR_BENCH_HF_CACHE", str(Path.home() / ".cache/huggingface/hub"))


_MODEL: dict[tuple, object] = {}  # одна модель на процесс: вторая копия turbo рядом с первой роняла память


def model_factory(device: str, compute: str):
    if device == "cuda" and Path(CUDA_DLLS).is_dir():
        os.add_dll_directory(CUDA_DLLS)
        os.environ["PATH"] = CUDA_DLLS + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel

    def load(size: str):
        key = (size, device, compute)
        if key not in _MODEL:
            _MODEL.clear()
            import gc

            gc.collect()
            _MODEL[key] = WhisperModel(size, device=device, compute_type=compute, download_root=HF_CACHE,
                                       local_files_only=True, cpu_threads=8)
        return _MODEL[key]

    return load


def results_dir(bench: Path) -> Path:
    p = bench / "results"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_profile(name: str, tag: str, bench: Path, device: str, compute: str, sets: set[str] | None) -> dict:
    from tools.asr_bench.profiles import PROFILES

    profile = PROFILES[name]
    items = [it for it in gen.load_manifest(bench)["items"] if not sets or it["set"] in sets]
    factory = model_factory(device, compute)
    out_path = results_dir(bench) / f"{name}__{tag}.json"
    done: dict[str, dict] = {}
    if out_path.is_file():
        done = json.loads(out_path.read_text("utf-8")).get("results", {})
    for k, it in enumerate(items):
        key = f"{it['set']}/{it['id']}"
        if key in done:
            continue
        t0 = time.perf_counter()
        res = profile(factory, Path(it["path"]))
        if res.get("skipped"):
            continue
        res["sec"] = round(time.perf_counter() - t0, 3)
        done[key] = res
        print(f"[{name}] {k + 1}/{len(items)} {key}: {res['text'][:80]!r} ({res['sec']} с)", flush=True)
    summary = metrics.summarize(gen.load_manifest(bench)["items"], done)
    payload = {"profile": name, "tag": tag, "device": device, "compute": compute, "summary": summary, "results": done}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    return summary


def report(bench: Path, tag: str | None) -> str:
    rows = []
    for p in sorted(results_dir(bench).glob("*.json")):
        data = json.loads(p.read_text("utf-8"))
        if tag and data.get("tag") != tag:
            continue
        s = data["summary"]
        ns, tail, clean = s.get("nospeech", {}), s.get("tail", {}), s.get("clean", {})
        music, dub = s.get("music", {}), s.get("dub", {})
        rows.append(f"| {data['profile']} | {data['tag']} | {ns.get('hallucinated', '–')}/{ns.get('clips', '–')} | "
                    f"{tail.get('clips_with_tail_text', '–')}/{tail.get('clips', '–')} ({tail.get('tail_words', '–')} сл.) | "
                    f"{_pct(clean.get('wer'))} | {_pct(tail.get('wer'))} | {_pct(music.get('wer'))} | {_pct(dub.get('wer'))} | "
                    f"{', '.join(clean.get('controls_lost', []) + tail.get('controls_lost', [])) or '—'} | {s.get('sec')} |")
    head = ("| профиль | метка | выдумки без речи | хвост: клипов с текстом (слов) | WER чистая | WER с хвостом | "
            "WER под музыкой | WER дубляж (к опоре) | потеряны контрольные фразы | время, с |\n|" + "---|" * 10)
    return head + "\n" + "\n".join(rows)


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.1f} %"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default=str(gen.BENCH_DIR))
    r = sub.add_parser("run")
    r.add_argument("--profile", required=True, nargs="+")
    r.add_argument("--tag", default="before")
    r.add_argument("--bench", default=str(gen.BENCH_DIR))
    r.add_argument("--device", default="cpu")
    r.add_argument("--compute", default="int8")
    r.add_argument("--sets", default="")
    rep = sub.add_parser("report")
    rep.add_argument("--bench", default=str(gen.BENCH_DIR))
    rep.add_argument("--tag", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        m = gen.build(Path(a.out))
        counts: dict[str, int] = {}
        for it in m["items"]:
            counts[it["set"]] = counts.get(it["set"], 0) + 1
        print(json.dumps(counts, ensure_ascii=False))
    elif a.cmd == "run":
        sets = {s for s in a.sets.split(",") if s} or None
        for name in a.profile:
            s = run_profile(name, a.tag, Path(a.bench), a.device, a.compute, sets)
            print(json.dumps({k: v for k, v in s.items()}, ensure_ascii=False)[:1500])
    else:
        print(report(Path(a.bench), a.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
