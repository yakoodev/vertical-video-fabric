"""Замер фильтра шума тиммейтов (app/meme_edit_team.py) на настоящих боях пилотов мем-эдита.

Звук игры (тиммейты + игра) и микрофон игрока распознаются тем же Whisper, что в мем-эдите (large-v3-turbo, VAD,
уверенность по сегментам — ``segment_stats``), результат кэшируется в ``--out``; потом фильтр печатает, какие
реплики тиммейтов оставлены и какие выброшены и почему.

    python tools/meme_team_filter_eval.py [--out D:/vvf-offload/tmp/teamfilter] [--only-cached]

Пары (звук игры, микрофон): pilot1 game.wav/mic.wav (8 мин) и вырезки боёв pilot2/wav (``<бой>_0_<с>.wav`` — игра,
``_1_`` — микрофон).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MEMES = Path("D:/vvf-offload/memes")
HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"


def pairs() -> list[tuple[str, Path, Path | None]]:
    out: list[tuple[str, Path, Path | None]] = [("pilot1", MEMES / "pilot1" / "game.wav", MEMES / "pilot1" / "mic.wav")]
    wav = MEMES / "pilot2" / "wav"
    for game in sorted(wav.glob("*_0_*.wav")):
        mic = game.with_name(game.name.replace("_0_", "_1_", 1))
        out.append((game.stem, game, mic if mic.is_file() else None))
    return out


def transcribe(path: Path, cache: Path) -> list[dict]:
    """Слова с уверенностью, как app/meme_edit_inputs._whisper (кэш — JSON рядом)."""
    file = cache / f"{path.parent.name}-{path.stem}.json"
    if file.is_file():
        return json.loads(file.read_text(encoding="utf-8"))
    from app import meme_edit_inputs as I
    from app.subtitles import whisper_local as WL

    def loader(_size: str):
        from faster_whisper import WhisperModel

        return WhisperModel("mobiuslabsgmbh/faster-whisper-large-v3-turbo", device="cpu", compute_type="int8",
                            cpu_threads=4, download_root=str(HF_HUB))

    provider = WL.WhisperSubtitleProvider(model_loader=loader)
    real = WL.WhisperSubtitleProvider.transcribe
    WL.WhisperSubtitleProvider.transcribe = lambda self, p, prof, model: provider._transcribe(p, prof, model)
    try:
        words = I._whisper(path)
    finally:
        WL.WhisperSubtitleProvider.transcribe = real
    file.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="D:/vvf-offload/tmp/teamfilter")
    ap.add_argument("--only-cached", action="store_true", help="не распознавать, только то, что уже в кэше")
    args = ap.parse_args()
    cache = Path(args.out)
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATA_DIR", str(cache / "data"))
    from app.meme_edit_fights import lines_from_words
    from app.meme_edit_inputs import drop_bleed
    from app.meme_edit_team import filter_team

    kept_n = dropped_n = 0
    reasons: dict[str, int] = {}
    for name, game, mic in pairs():
        if args.only_cached and not (cache / f"{game.parent.name}-{game.stem}.json").is_file():
            continue
        team = transcribe(game, cache)
        owner = transcribe(mic, cache) if mic else []
        team = drop_bleed(team, owner)
        kept, dropped = filter_team(team, owner)
        print(f"== {name}: тиммейты {len(lines_from_words(team))} реплик → оставлено {len(lines_from_words(kept))}")
        for ln in lines_from_words(kept):
            print(f"   +  {ln['start']:7.2f}  {ln['text']}")
        for d in dropped:
            print(f"   -  {d['start']:7.2f}  {d['text']}   [{d['why']}]")
            reasons[d["why"].split(":")[0]] = reasons.get(d["why"].split(":")[0], 0) + 1
        kept_n += len(lines_from_words(kept))
        dropped_n += len(dropped)
    print(f"итого: оставлено {kept_n}, выброшено {dropped_n}; причины: {reasons}")


if __name__ == "__main__":
    main()
