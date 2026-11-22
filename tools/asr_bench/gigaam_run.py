"""GigaAM-v3 (Сбер, MIT) на записях стенда — запускается ОТДЕЛЬНЫМ окружением, не .venv сервиса:

    D:/vvf-offload/tools/gigaam_env/Scripts/python.exe -I tools/asr_bench/gigaam_run.py --rev e2e_rnnt

Окружение: torch/torchaudio 2.8 CPU, transformers 4.57, hydra-core, sentencepiece (без pyannote: длинные записи режем
сами окнами по 20 с, без детектора голоса — модель сама видит тишину, как в ролике). Веса — huggingface
``ai-sage/GigaAM-v3`` (ревизии ctc / rnnt / e2e_ctc / e2e_rnnt, ~440 МБ каждая), кэш — ``HF_HOME`` на D.
Результат — тот же формат, что у run.py: ``results/gigaam_<rev>__<tag>.json`` (слова — по окнам: время слова = начало
окна, этого хватает метрике «текст в хвосте»)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", "D:/vvf-offload/tools/gigaam_env/hf")
WINDOW_SEC = 20.0
RATE = 16000


def load_asr(rev: str):
    """Модель из файлов huggingface без ``AutoModel(trust_remote_code)``: transformers при загрузке требует pyannote
    (нужен только длинной записи через их VAD) — модуль модели импортируем сами, веса кладём по ключам ``model.*``."""
    import importlib
    import json as _json

    import torch
    from huggingface_hub import hf_hub_download
    from hydra.utils import instantiate

    repo = "ai-sage/GigaAM-v3"
    cfg_path = hf_hub_download(repo, "config.json", revision=rev)
    code_path = hf_hub_download(repo, "modeling_gigaam.py", revision=rev)
    weights = hf_hub_download(repo, "pytorch_model.bin", revision=rev)
    cfg = _json.loads(Path(cfg_path).read_text("utf-8"))["cfg"]["model"]
    if "model_path" in (cfg["cfg"].get("decoding") or {}):
        cfg["cfg"]["decoding"]["model_path"] = hf_hub_download(repo, "tokenizer.model", revision=rev)
    sys.path.insert(0, str(Path(code_path).parent))
    importlib.import_module("modeling_gigaam")
    model = instantiate(cfg, _recursive_=False)
    state = torch.load(weights, map_location="cpu", weights_only=True)
    state = {k[len("model."):]: v for k, v in state.items() if k.startswith("model.")}
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise RuntimeError(f"веса GigaAM не сошлись: missing={missing[:5]} unexpected={unexpected[:5]}")
    return model.eval()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="e2e_rnnt")
    ap.add_argument("--tag", default="before")
    ap.add_argument("--bench", default="D:/vvf-offload/asr_bench")
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()

    import numpy as np
    import torch

    torch.set_num_threads(a.threads)
    sys.path.insert(0, str(HERE))
    import metrics  # noqa: E402 - метрики стенда (чистый python, без app)

    asr = load_asr(a.rev)
    bench = Path(a.bench)
    items = json.loads((bench / "manifest.json").read_text("utf-8"))["items"]
    out_path = bench / "results" / f"gigaam_{a.rev}__{a.tag}.json"
    done: dict = json.loads(out_path.read_text("utf-8"))["results"] if out_path.is_file() else {}
    for k, it in enumerate(items):
        key = f"{it['set']}/{it['id']}"
        if key in done:
            continue
        import wave

        with wave.open(it["path"]) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        t0 = time.perf_counter()
        words, texts = [], []
        win = int(WINDOW_SEC * RATE)
        with torch.inference_mode():
            for s in range(0, max(1, len(x)), win):
                chunk = x[s:s + win]
                if len(chunk) < RATE // 10:  # обрывок короче 0.1 с — не окно
                    continue
                wav = torch.from_numpy(chunk.copy()).unsqueeze(0)
                length = torch.full([1], wav.shape[-1])
                enc, enc_len = asr.forward(wav, length)
                text = asr.decoding.decode(asr.head, enc, enc_len)[0].strip()
                if text:
                    texts.append(text)
                    words += [{"word": t, "start": s / RATE, "end": s / RATE + 0.1} for t in text.split()]
        res = {"text": " ".join(texts), "words": words, "segments": [], "sec": round(time.perf_counter() - t0, 3)}
        done[key] = res
        print(f"[gigaam_{a.rev}] {k + 1}/{len(items)} {key}: {res['text'][:80]!r} ({res['sec']} с)", flush=True)
    summary = metrics.summarize(items, done)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"profile": f"gigaam_{a.rev}", "tag": a.tag, "device": "cpu", "compute": "fp32",
                                    "summary": summary, "results": done}, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(summary, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
