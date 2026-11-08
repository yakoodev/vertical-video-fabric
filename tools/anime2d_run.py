"""Способ «AI-аниме 2D» от пресета до mp4. Инструкция: docs/workflows/ai-anime-2d.md.

    python tools/anime2d_run.py estimate PRESET            # смета картинок, 0 ₽
    python tools/anime2d_run.py balance  PRESET            # баланс Polza (бесплатно)
    python tools/anime2d_run.py generate PRESET --max-rub 50 [--dry-run] [--only id,id] [--force]
    python tools/anime2d_run.py preview  PRESET 0.5,4.2,8.8 # отдельные кадры PNG в OUT/preview
    python tools/anime2d_run.py render   PRESET [--no-captions] [--no-patches]
    python tools/anime2d_run.py all      PRESET --max-rub 50 [--dry-run]   # generate + render

PRESET — путь к JSON или имя из tools/anime2d_presets (например ``tema_yawn``).
``--src`` / ``--out`` переопределяют paths пресета. Платный шаг без ``--max-rub`` не запускается.
Результат render: OUT/ai_test.mp4, OUT/ai_sheet.jpg (лист кадров), OUT/ai_still.png, OUT/ai_work/raw.mp4.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import anime2d_polza as G  # noqa: E402
from tools import anime2d_preset as P  # noqa: E402

PRESETS = ROOT / "tools" / "anime2d_presets"


def _log(msg: str) -> None:
    print(f"[anime2d {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_preset(name: str) -> Path:
    p = Path(name)
    if p.is_file():
        return p
    cand = PRESETS / (name if name.endswith(".json") else f"{name}.json")
    if cand.is_file():
        return cand
    raise SystemExit(f"пресет не найден: {name} (есть: {', '.join(x.stem for x in PRESETS.glob('*.json'))})")


def to_u8(frame: np.ndarray) -> np.ndarray:
    return (frame * 255 + 0.5).astype(np.uint8)


def render_frames(scene, preset: dict, out: Path) -> float:
    o = preset["output"]
    W, H, fps = o["width"], o["height"], o["fps"]
    n = int(round(o["duration"] * fps))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for i in range(n):
        proc.stdin.write(to_u8(scene.render(i / fps)).tobytes())
        if i % 30 == 0:
            _log(f"кадр {i}/{n}")
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg не собрал видео")
    return time.time() - t0


def captions(preset: dict, video: Path, out: Path, work: Path) -> None:
    """Субтитры стилем «Историй» (моушн-компоновщик, нужен собранный node/motion-render.mjs)."""
    from tools.vtubika_story import burn_captions

    cap = preset.get("captions") or {}
    burn_captions({"highlight": cap.get("highlight", {})}, P.caption_words(preset), video, out, work,
                  preset["output"]["duration"])


def add_music(preset: dict, video: Path, out: Path) -> None:
    mu = preset.get("music") or {}
    dur = preset["output"]["duration"]
    path = Path(mu.get("file", ""))
    if not mu or not path.is_file():
        shutil.copyfile(video, out)
        return
    af = (f"volume={mu.get('volume', 0.55)},afade=t=in:d={mu.get('fade_in', 0.4)},"
          f"afade=t=out:st={dur - mu.get('fade_out', 1.2)}:d={mu.get('fade_out', 1.2)},loudnorm=I=-18:TP=-1.5")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(path), "-filter_complex",
                    f"[1:a]{af}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-t", f"{dur}", str(out)], check=True)


def sheet(video: Path, out: Path, duration: float, n: int = 12, cols: int = 4) -> None:
    """Лист n кадров с подписью времени — смотреть, не слайд-шоу ли это."""
    tiles = []
    for i in range(n):
        t = (i + 0.5) * duration / n
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=360:640", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True).stdout
        img = np.frombuffer(p, np.uint8).reshape(640, 360, 3).copy()
        cv2.putText(img, f"{t:.2f}s", (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, f"{t:.2f}s", (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(img)
    rows = [np.concatenate(tiles[r * cols:(r + 1) * cols], 1) for r in range(math.ceil(n / cols))]
    cv2.imwrite(str(out), cv2.cvtColor(np.concatenate(rows, 0), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])


def render(preset: dict, src: Path, out: Path, with_captions: bool = True, patches: bool = True) -> Path:
    from tools.anime2d_scene import Scene

    missing = [im["file"] for im in preset["generation"]["images"] if not (src / im["file"]).is_file()]
    if missing:
        raise SystemExit(f"нет картинок в {src}: {', '.join(missing)} — сначала generate")
    work = out / "ai_work"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    scene = Scene(preset, src, patches=patches)
    raw = work / "raw.mp4"
    secs = render_frames(scene, preset, raw)
    _log(f"кадры за {secs:.0f} с")
    capt = work / "caps.mp4"
    if with_captions and preset.get("captions"):
        try:
            captions(preset, raw, capt, work)
        except Exception as exc:  # субтитры не должны ронять ролик — пишем в лог и едем дальше
            _log(f"субтитры не наложены: {exc}")
            shutil.copyfile(raw, capt)
    else:
        shutil.copyfile(raw, capt)
    final = out / "ai_test.mp4"
    add_music(preset, capt, final)
    o = preset["output"]
    n, cols = o.get("sheet", [12, 4])
    sheet(final, out / "ai_sheet.jpg", o["duration"], n, cols)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{o.get('still', o['duration'] / 2):.3f}", "-i", str(final),
                    "-frames:v", "1", str(out / "ai_still.png")], check=True)
    _log(f"готово: {final} за {time.time() - t0:.0f} с")
    return final


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["estimate", "balance", "generate", "preview", "render", "all"])
    ap.add_argument("preset")
    ap.add_argument("times", nargs="?", default="", help="для preview: времена через запятую")
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--max-rub", type=float, default=None, help="потолок трат на этот запуск (обязателен для трат)")
    ap.add_argument("--dry-run", action="store_true", help="только смета, без трат")
    ap.add_argument("--only", default="", help="id картинок через запятую")
    ap.add_argument("--force", action="store_true", help="перегенерировать даже если файл есть (платно!)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--no-patches", action="store_true", help="без правок картинок кодом (сверка со старым)")
    a = ap.parse_args(argv)
    preset = P.load(find_preset(a.preset))
    paths = preset.get("paths", {})
    src = Path(a.src or paths.get("src") or "ai_src")
    out = Path(a.out or paths.get("out") or src.parent)
    only = [x for x in a.only.split(",") if x] or None
    if a.cmd == "estimate":
        G.print_plan(preset, P.plan(preset, src, only, a.force))
        print(f"справка: полный набор этого пресета стоил {P.spent_before(preset):.1f} ₽ вместе с переделками")
        return 0
    if a.cmd == "balance":
        G.prepare(preset["generation"]["container"])
        print(f"баланс Polza: {G.balance(preset['generation']['container'])}")
        return 0
    if a.cmd in ("generate", "all"):
        if not a.dry_run and a.max_rub is None:
            raise SystemExit("платный шаг: укажите --max-rub (или --dry-run для сметы)")
        try:
            G.generate(preset, src, a.max_rub or 0.0, dry_run=a.dry_run, only=only, force=a.force)
        except G.SpendError as exc:
            raise SystemExit(f"остановлено: {exc}") from exc
        if a.cmd == "generate" or (a.dry_run and P.plan(preset, src)):
            return 0
    if a.cmd == "preview":
        from tools.anime2d_scene import Scene

        scene = Scene(preset, src, patches=not a.no_patches)
        pdir = out / "preview"
        pdir.mkdir(parents=True, exist_ok=True)
        for s in a.times.split(","):
            t = float(s)
            cv2.imwrite(str(pdir / f"p_{t:05.2f}.png"), cv2.cvtColor(to_u8(scene.render(t)), cv2.COLOR_RGB2BGR))
        _log(f"превью → {pdir}")
        return 0
    render(preset, src, out, with_captions=not a.no_captions, patches=not a.no_patches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
