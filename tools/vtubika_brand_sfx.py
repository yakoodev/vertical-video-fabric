"""Звуки бренда «Витубики» для концовки: аккорд калимбы на появление значка и короткий щипок на точке над «k».

    python tools/vtubika_brand_sfx.py D:/vvf-offload/vtubika/endcard_stock [--out assets/brand/vtubika/sfx]

Исходники — превью Freesound (CC0, см. ``assets/brand/vtubika/sfx/LICENSES.md``), лежат вне репозитория:

* ``ogg2/10_kalimba_chord.ogg`` — аккорд, выбранный владельцем (раунд 2), копируется как есть;
* ``raw2/fs_732291.mp3`` — тот же аккорд без обработки (kk_kalimba_chord3, ssud_motion);
* ``raw3/fs_732284.mp3`` — одиночная нота G4 той же калимбы из того же пака (kk_Kalimba3, ssud_motion).

Варианты звука точки (все — одна нота, без мелодии и мерцания, тише аккорда на ~6 дБ, тёплые):

* **A** ``dot_a_note.ogg`` — нота B4 (самая громкая в аккорде) вырезана из записи аккорда узкой полосой (3 × bandpass
  Q 6) + щелчок пальца по язычку (первые 40 мс аккорда выше 1.2 кГц), тон выше на 5 полутонов (E5, нота аккорда),
  0.5 с с мягким затуханием;
* **B** ``dot_b_pluck.ogg`` — настоящая одиночная нота той же калимбы (G4) выше на 5 полутонов (C5, основной тон
  аккорда), 0.5 с;
* **C** ``dot_c_tap.ogg`` — A через ФНЧ 3 кГц (2 × 12 дБ/окт) с быстрым спадом: 0.32 с, ещё на 3 дБ тише — больше
  «тук», чем нота.

Скрипт печатает замеры (длина, атака, громкость, центроиды, доля 6–12 кГц) — те же, что в SOURCES.md раундов 1–2.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR = 48000
SEMITONES = 5
DOT_DB = -6.0  # точка тише аккорда (по громким 50 мс)
TAP_DB = -9.0
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def decode(path: Path, af: str | None = None) -> np.ndarray:
    """Файл → float32 (n, 2) 48 кГц, с фильтрами ffmpeg ``af``."""
    args = ["ffmpeg", "-v", "error", "-i", str(path), *(["-af", af] if af else []), "-ac", "2", "-ar", str(SR),
            "-f", "f32le", "-"]
    raw = subprocess.run(args, capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def encode(x: np.ndarray, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", "-c:a",
                    "libvorbis", "-q:a", "6", str(out)], input=x.astype(np.float32).tobytes(), check=True)


def onset(x: np.ndarray, rel_db: float = -20.0) -> float:
    """Начало атаки: первый 1-мс блок, где пик выше максимума файла на ``rel_db``."""
    m = np.abs(x).max(axis=1)
    blk = SR // 1000
    env = m[: len(m) // blk * blk].reshape(-1, blk).max(axis=1)
    return float(np.argmax(env >= env.max() * 10 ** (rel_db / 20)) / 1000)


def shift(x: np.ndarray, semitones: float) -> np.ndarray:
    """Тон выше «как у короткого язычка»: ускорение записи (длина и затухание короче, без растяжения)."""
    ratio = 2 ** (semitones / 12)
    n = int(len(x) / ratio)
    src = np.arange(n) * ratio
    return np.stack([np.interp(src, np.arange(len(x)), x[:, c]) for c in range(2)], axis=1).astype(np.float32)


def envelope(n: int, fade_in: float, hold: float, fade: float) -> np.ndarray:
    """Нарастание ``fade_in`` с, держать до ``hold``, затем плавный спад (cos²) до нуля за ``fade`` с."""
    t = np.arange(n) / SR
    env = np.clip(t / max(fade_in, 1e-4), 0, 1)
    tail = np.clip((t - hold) / fade, 0, 1)
    return (env * np.cos(tail * np.pi / 2) ** 2)[:, None].astype(np.float32)


def rms_peak_db(x: np.ndarray, win: float = 0.05) -> float:
    """Громкие 50 мс (как ``astats`` RMS peak у app.motion_render.audio_levels)."""
    m = x.mean(axis=1)
    w = int(SR * win)
    sq = np.convolve(m ** 2, np.ones(w) / w, mode="valid")
    return float(10 * np.log10(sq.max() + 1e-12))


def build_a(chord_raw: Path) -> tuple[np.ndarray, np.ndarray]:
    """(A, A до затухания) из записи аккорда: нота B4 узкой полосой + щелчок атаки, +5 полутонов."""
    # Аккорд — «перебором»: B4 звучит на ~85 мс позже первой ноты, поэтому начало — по атаке самой B4, и щелчок
    # берётся там же (иначе два удара «т-дум»).
    note = decode(chord_raw, ",".join(["bandpass=f=494:width_type=q:w=6"] * 3))
    t0 = max(0.0, onset(note, -12.0) - 0.008)
    seg = slice(int(t0 * SR), int((t0 + 1.0) * SR))
    note = note[seg]
    click = decode(chord_raw, "highpass=f=1200,highpass=f=1200,lowpass=f=7000")[seg]
    click *= envelope(len(click), 0.001, 0.0, 0.04)
    note /= np.abs(note).max()
    click /= np.abs(click).max()
    raw = shift(note + 0.22 * click, SEMITONES)
    raw = decode_filtered(raw, "highpass=f=150,lowpass=f=9000")
    return raw[: int(0.5 * SR)] * envelope(int(0.5 * SR), 0.003, 0.12, 0.38), raw


def build_b(pluck_raw: Path) -> np.ndarray:
    full = decode(pluck_raw, "highpass=f=150,lowpass=f=9000")
    t0 = max(0.0, onset(full) - 0.004)
    x = shift(full[int(t0 * SR): int((t0 + 1.0) * SR)], SEMITONES)
    return x[: int(0.5 * SR)] * envelope(int(0.5 * SR), 0.003, 0.12, 0.38)


def build_c(a_raw: np.ndarray) -> np.ndarray:
    x = decode_filtered(a_raw, "lowpass=f=3000,lowpass=f=3000")
    n = int(0.32 * SR)
    t = np.arange(n) / SR
    return x[:n] * (np.exp(-t / 0.07)[:, None] * envelope(n, 0.002, 0.2, 0.12)).astype(np.float32)


def decode_filtered(x: np.ndarray, af: str) -> np.ndarray:
    """Массив через фильтры ffmpeg."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", "-af", af,
                          "-f", "f32le", "-"], input=x.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def level_to(x: np.ndarray, target_db: float) -> np.ndarray:
    return x * np.float32(10 ** ((target_db - rms_peak_db(x)) / 20))


def measure(path: Path) -> dict:
    """Замеры готового файла: длина, атака, LUFS, пик, громкие 50 мс, центроиды, доля 6–12 кГц, основной тон."""
    x = decode(path)
    a = onset(x)
    m = x.mean(axis=1)[int(a * SR): int((a + 3.0) * SR)]
    X = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    f = np.fft.rfftfreq(len(m), 1 / SR)
    P = X ** 2
    band = (f >= 6000) & (f <= 12000)
    f0 = float(f[np.argmax(X * (f > 80))])
    midi = 69 + 12 * np.log2(f0 / 440)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null",
                          "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    lufs = re.findall(r"I:\s*(-?[\d.]+) LUFS", err)
    tp = re.findall(r"Peak:\s*(-?[\d.]+) dBFS", err)
    return {"file": path.name, "dur": round(len(x) / SR, 3), "onset": round(a, 3), "hit": round(onset(x, -6.0), 3),
            "lufs": float(lufs[-1]) if lufs else None, "true_peak": float(tp[-1]) if tp else None,
            "rms50_peak": round(rms_peak_db(x), 1),
            "centroid_ampl": round(float((f * X).sum() / X.sum())), "centroid_power": round(float((f * P).sum() / P.sum())),
            "hf_6_12k_pct": round(100 * float(P[band].sum() / P.sum()), 4),
            "f0": round(f0, 1), "note": f"{NAMES[int(round(midi)) % 12]}{int(round(midi)) // 12 - 1}",
            "kb": round(path.stat().st_size / 1024, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="звуки бренда Витубики для концовки")
    ap.add_argument("stock", help="папка endcard_stock (ogg2/, raw2/, raw3/)")
    ap.add_argument("--out", default=str(ROOT / "assets" / "brand" / "vtubika" / "sfx"))
    args = ap.parse_args(argv)
    stock, out = Path(args.stock), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chord = out / "kalimba_chord.ogg"
    shutil.copyfile(stock / "ogg2" / "10_kalimba_chord.ogg", chord)
    ref = rms_peak_db(decode(chord))
    a, a_raw = build_a(stock / "raw2" / "fs_732291.mp3")
    encode(level_to(a, ref + DOT_DB), out / "dot_a_note.ogg")
    encode(level_to(build_b(stock / "raw3" / "fs_732284.mp3"), ref + DOT_DB), out / "dot_b_pluck.ogg")
    encode(level_to(build_c(a_raw), ref + TAP_DB), out / "dot_c_tap.ogg")
    report = [measure(out / n) for n in ("kalimba_chord.ogg", "dot_a_note.ogg", "dot_b_pluck.ogg", "dot_c_tap.ogg")]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
