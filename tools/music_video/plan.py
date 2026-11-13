# -*- coding: utf-8 -*-
"""Шаг plan: черновик plan.json по анализу песни — сцены по 2 такта на сетке, чередование движений камеры,
кандидаты в «большие слова» (длинные и повторяющиеся), подписи на все слова, заготовки картинок.

Черновик — отправная точка: промпты картинок, персонажи, большие слова и шутки правятся руками (см. docs/music-video.md)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

MOVES = ["push", "orbit", "truck_l", "crane_up", "zoom", "truck_r", "orbit", "pull"]

IMAGE_STYLE = {
    "comic": {
        "common": "Style: flat retro comic-book cartoon illustration, thick bold black ink outlines, cel shading with halftone dot "
                  "shadows, limited palette of deep purple, cream, warm orange, mustard yellow and brown, printed comic paper feel, "
                  "funny exaggerated cartoon proportions. No text, no letters, no speech bubbles, no watermark, no border, no panel frame.",
        "plate": "Vertical 9:16 composition, full-bleed background scene.",
        "cutout": "Single character sticker, full body from head to feet, centred with generous empty margin.",
    },
    "paper": {
        "common": "Style: dark gothic cinematic photograph, black-and-white monochrome with deep crimson red as the ONLY colour accent, "
                  "rich deep blacks, soft silver highlights, analog film grain, atmospheric fog, moody low-key light, highly detailed, "
                  "realistic. No text, no letters, no watermark, no border.",
        "plate": "Vertical 9:16 composition.",
        "cutout": "Full subject visible, sharp clean silhouette edges.",
    },
}


def draft(an: dict, style: str, n_images: int = 6) -> dict:
    words = an["words"]
    bars = [b for b in an["downbeats"] if an["start"] <= b < an["end"]]
    # сцена = 2 такта; первая — с начала участка
    cuts = [an["start"]] + [b for i, b in enumerate(bars) if i % 2 == 0 and b - an["start"] > 1.0]
    cuts = [c for i, c in enumerate(cuts) if i == 0 or c - cuts[i - 1] > 1.5]
    freq = Counter(w["w"].lower() for w in words)
    scenes = []
    for k, c0 in enumerate(cuts):
        c1 = cuts[k + 1] if k + 1 < len(cuts) else an["end"]
        idx = [i for i, w in enumerate(words) if c0 <= w["on"] < c1]
        sc = {"id": f"s{k:02d}", "from": round(c0, 3),
              "bg": f"bg{k % n_images}" if k % 3 != 2 else {"burst": True, "bg": ["cream", "yellow", "orange"][k % 3], "line": "purple"},
              "camera": {"move": MOVES[k % len(MOVES)], "amount": 1.0},
              "transition": ["cut", "panel", "burst"][k % 3]}
        if idx:
            sc["captions"] = {"from": f"w:{idx[0]}", "to": f"w:{idx[-1]}", "y": 0.075}
            # большое слово: самое длинное или самое повторяемое в сцене
            best = max(idx, key=lambda i: (len(words[i]["w"]) >= 6) + freq[words[i]["w"].lower()] / 4 + len(words[i]["w"]) / 20)
            sc["words"] = [{"text": words[best]["w"].upper(), "at": [f"w:{best}"], "y": 0.27, "w": 0.84}]
        scenes.append(sc)
    images = {f"bg{i}": {"kind": "plate", "prompt": "TODO: describe the background scene"} for i in range(n_images)}
    return {"style": style, "fps": 30, "size": [1080, 1920], "censor": [], "lyrics_fix": {},
            "characters": {}, "image_style": IMAGE_STYLE.get(style, IMAGE_STYLE["comic"]), "images": images, "scenes": scenes}


def main(an: dict, out: Path, style: str) -> Path:
    p = out / "plan.json"
    if p.exists():
        print("  plan.json уже есть — не перезаписываю:", p)
        return p
    json.dump(draft(an, style), open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  черновик плана:", p, "— впишите промпты картинок, персонажей и шутки, затем шаг images")
    return p
