# -*- coding: utf-8 -*-
"""Шаг check: лист кадров (N кадров сеткой), поиск «застывших» отрезков, движение по секундам.

    python tools/music_video/check.py VIDEO [SHEET.jpg] [--n 24]
    python tools/music_video/check.py --dir FRAMES_DIR SHEET.jpg   (лист из готовых PNG, например стоп-кадров)"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


def grid(cells: list[np.ndarray], cols: int) -> np.ndarray:
    rows = []
    for r in range(0, len(cells), cols):
        row = cells[r:r + cols]
        while len(row) < cols:
            row.append(np.zeros_like(cells[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def label(im: np.ndarray, text: str) -> np.ndarray:
    cv2.putText(im, text, (6, 22), 0, 0.6, (0, 0, 0), 4)
    cv2.putText(im, text, (6, 22), 0, 0.6, (255, 255, 255), 2)
    return im


def sheet_from_dir(d: Path, out: Path, cols: int = 6) -> None:
    files = sorted(d.glob("*.png"))
    cells = [label(cv2.resize(cv2.imread(str(f)), (270, 480), interpolation=cv2.INTER_AREA), f.stem) for f in files]
    cv2.imwrite(str(out), grid(cells, cols), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("sheet", out, len(cells))


def check_video(video: Path, sheet: Path, n: int = 24, fps: float = 30.0, static_thr: float = 0.6) -> dict:
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    want = {int(round(i * (total - 1) / (n - 1))) for i in range(n)}
    cells, prev, d, i = {}, None, [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        sm = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (135, 240)).astype(np.float32)
        d.append(0.0 if prev is None else float(np.abs(sm - prev).mean()))
        prev = sm
        if i in want:
            cells[i] = label(cv2.resize(fr, (270, 480), interpolation=cv2.INTER_AREA), f"{i / fps:.1f}s")
        i += 1
    runs, run, start = [], 0, 0
    for k, x in enumerate(d):
        if x < static_thr:
            if run == 0:
                start = k
            run += 1
        else:
            if run > fps:
                runs.append((start / fps, run / fps))
            run = 0
    if run > fps:
        runs.append((start / fps, run / fps))
    cv2.imwrite(str(sheet), grid([cells[k] for k in sorted(cells)], 8), [cv2.IMWRITE_JPEG_QUALITY, 88])
    per_sec = [round(float(np.mean(d[s:s + int(fps)])), 2) for s in range(0, len(d), int(fps))]
    print(f"кадров {len(d)}; застывшие отрезки > 1 с: {[(round(a, 2), round(b, 2)) for a, b in runs] or 'нет'}")
    print("движение по секундам:", per_sec)
    print("лист:", sheet)
    return {"frames": len(d), "static": runs, "motion": per_sec}


if __name__ == "__main__":
    if sys.argv[1] == "--dir":
        sheet_from_dir(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        v = Path(sys.argv[1])
        check_video(v, Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else v.with_name("sheet.jpg"),
                    int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 24)
