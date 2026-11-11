"""Озвучка мем-роликов «3D-анимация с мемами»: одна платная проба ElevenLabs (Polza) на весь сценарий, проверка
Whisper и нарезка на реплики — монтаж сам решает, где какая реплика звучит (паузы, «заморозка», панчлайн).

    python tools/meme3d_voice.py tts D:/vvf-offload/memes/pilot1/work --voice callum --take 1
    python tools/meme3d_voice.py words D:/vvf-offload/memes/pilot1/work/take1_callum.mp3

Потолок трат — ``CAP_RUB`` на прогон (оценка до вызова по прайсу ×2 запаса, факт — из usage.cost_rub и баланса).
Кэш: файл пробы уже есть — повторный вызов ничего не тратит.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CAP_RUB = 10.0
MAIN_ROOT = ROOT.parent.parent.parent if ".claude" in str(ROOT) else ROOT


def load_env() -> None:
    env = MAIN_ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("POLZA_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def balance() -> float | None:
    import httpx

    key = os.environ.get("POLZA_API_KEY", "")
    base = os.environ.get("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/")
    try:
        r = httpx.get(f"{base}/balance", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        return float(r.json()["amount"])
    except Exception:  # noqa: BLE001 - баланс только для отчёта
        return None


def tts(text: str, out: Path, voice: str, model: str = "elevenlabs/eleven-v4", list_price: float = 9525.888) -> dict:
    """Одна проба. Оценка — прайс Polza ×2 (замер 2026-11-01 был вдвое выше прайса), выше потолка — отказ."""
    meta = out.with_suffix(".json")
    if out.is_file() and meta.is_file():
        return json.loads(meta.read_text("utf-8"))
    load_env()
    from app.dubbing import PolzaTTS

    estimate = len(text) * list_price / 1e6 * 2
    if estimate > CAP_RUB:
        raise SystemExit(f"оценка {estimate:.2f} ₽ выше потолка {CAP_RUB} ₽")
    before = balance()
    speech = PolzaTTS(model=model, voice=voice).synthesize(text, out)
    after = balance()
    info = {"voice": voice, "model": model, "chars": len(text), "estimate_rub": round(estimate, 3),
            "cost_rub": speech.cost_rub, "usage": speech.raw_usage, "balance_before": before, "balance_after": after,
            "balance_delta": round(before - after, 4) if before is not None and after is not None else None, "text": text}
    meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    return info


def words(path: Path) -> list[dict]:
    """Слова с временем: faster-whisper large-v3-turbo из локального кэша HF, CPU int8, русский."""
    import numpy as np
    from faster_whisper import WhisperModel

    cache = path.with_suffix(".words.json")
    if cache.is_file():
        return json.loads(cache.read_text("utf-8"))
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cpu", compute_type="int8",
                         cpu_threads=4)
    segs, _ = model.transcribe(audio, language="ru", word_timestamps=True, vad_filter=False, beam_size=5)
    out = []
    for s in segs:
        for w in s.words or []:
            if w.word.strip():
                out.append({"w": w.word.strip(), "t0": round(float(w.start), 3), "t1": round(float(w.end), 3),
                            "p": round(float(w.probability), 3)})
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=0), "utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("tts")
    a.add_argument("work")
    a.add_argument("--voice", required=True)
    a.add_argument("--take", type=int, required=True)
    a.add_argument("--text", required=True, help="файл с текстом")
    b = sub.add_parser("words")
    b.add_argument("audio")
    args = ap.parse_args()
    if args.cmd == "tts":
        if not 1 <= args.take <= 3:
            raise SystemExit("не больше трёх проб")
        text = Path(args.text).read_text("utf-8").strip()
        out = Path(args.work) / f"take{args.take}_{args.voice}.mp3"
        print(json.dumps(tts(text, out, args.voice), ensure_ascii=False, indent=1))
    else:
        ws = words(Path(args.audio))
        print(" ".join(f"{w['w']}[{w['t0']:.2f}-{w['t1']:.2f}]" for w in ws))
    return 0


if __name__ == "__main__":
    sys.exit(main())
