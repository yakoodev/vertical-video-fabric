"""Сравнение бэкендов рендера 3D-серии на отрывке готового задания: Blender (Eevee) и threejs (Blender ставит позы,
three.js рисует) — те же планы, рядом в одном mp4, лист кадров, время и пик памяти.

    python tools/series3d_compare.py --job D:/vvf-offload/render3d/job-2/job.json --shots 4-8 \
        --out D:/vvf-offload/tmp/probe3d/excerpt [--backends blender,threejs] [--quality fast]

По очереди, по одному тяжёлому процессу (Blender — один процесс, Chromium — один). Кэш кадров у каждого бэкенда свой
(``<out>/<бэкенд>/cache``): повторный запуск дорисует только недостающее. Итог: ``<out>/excerpt_compare.mp4`` (2160×1920),
``excerpt_compare_small.mp4`` (≤ 19 МБ), ``excerpt_sheet.jpg``, ``compare.json`` (время на кадр, всего, память).
Нужны MOTION_PACKS_DIR (паки KayKit), Blender, node, ffmpeg.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import series3d_blender as B  # noqa: E402

FONT = "C:/Windows/Fonts/bahnschrift.ttf"
LABEL = {"blender": "Blender Eevee", "threejs": "Blender поза + three.js"}


def shots_arg(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def watch(out: Path) -> subprocess.Popen | None:
    """Пик памяти этого процесса и его потомков (Blender, node, Chromium) — tools/memwatch.ps1, только Windows.
    Остановка — файл ``<out>.stop`` (наблюдатель пишет итог и выходит сам)."""
    if os.name != "nt":
        return None
    stop = out.with_suffix(".stop")
    stop.unlink(missing_ok=True)
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools" / "memwatch.ps1"),
                             "-Root", str(os.getpid()), "-Out", str(out), "-Stop", str(stop)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def unwatch(w: subprocess.Popen | None, out: Path) -> dict | None:
    if w is None:
        return None
    out.with_suffix(".stop").write_text("1")
    try:
        w.wait(timeout=60)
    except subprocess.TimeoutExpired:
        w.kill()   # только свой наблюдатель (PID из Popen)
        return None
    out.with_suffix(".stop").unlink(missing_ok=True)
    try:
        return json.loads(out.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return None


def run_backend(job: dict, name: str, shots: list[int], out: Path) -> dict:
    work = out / name
    work.mkdir(parents=True, exist_ok=True)
    j = {**job, "render_backend": name}
    mp4 = out / f"{name}.mp4"
    t0 = time.time()
    last = [0.0]

    def progress(p: float, note: str) -> None:
        if time.time() - last[0] > 5 or p >= 1:
            last[0] = time.time()
            print(f"  [{name}] {p * 100:5.1f}% {note}", flush=True)

    info = B.render_local(j, mp4, work=work, cache=work / "cache", only=shots, workers=1, progress=progress)
    wall = time.time() - t0
    rep = info.get("report") or {}
    row = {"backend": name, "mp4": str(mp4), "frames": info["frames"], "rendered": info["rendered"], "wall_sec": round(wall, 1),
           "sec_per_frame": round(wall / max(1, info["rendered"]), 3) if info["rendered"] else None,
           "encode_sec": info.get("encode_sec"), "setup_sec": rep.get("setup_sec"), "render_sec": rep.get("render_sec")}
    if name == "threejs":
        row["threejs"] = rep.get("threejs")
        tim = work / "three_timing.json"
        if tim.is_file():
            t = json.loads(tim.read_text("utf-8"))
            row["three"] = {"wall_ms": t.get("wall_ms"), "ms_per_frame": t.get("ms_per_frame"),
                            "render_ms_per_frame": round(t["render_ms"] / max(1, t["frames"]), 1),
                            "shot_ms_per_frame": round(t["shot_ms"] / max(1, t["frames"]), 1), "load_ms": t.get("load_ms"),
                            "black": t.get("black"), "chunks": [{k: c.get(k) for k in ("n", "frames", "lights", "shadows", "fog", "steps")}
                                                                for c in t.get("chunks") or []]}
    row["checks"] = [{k: s.get(k) for k in ("n", "kind", "face_in", "samples", "show")} for s in rep.get("shots") or []
                     if s.get("n") in shots]
    return row


def side_by_side(a: Path, b: Path, out: Path, labels: tuple[str, str]) -> None:
    font = FONT.replace(":", "\\:")
    dt = lambda t: (f"drawtext=fontfile='{font}':text='{t}':x=28:y=28:fontsize=44:fontcolor=white:"  # noqa: E731
                    "box=1:boxcolor=black@0.55:boxborderw=14")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a), "-i", str(b), "-filter_complex",
                    f"[0:v]{dt(labels[0])}[l];[1:v]{dt(labels[1])}[r];[l][r]hstack=inputs=2,format=yuv420p[v]",
                    "-map", "[v]", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart", str(out)], check=True)


def small_copy(src: Path, out: Path, limit_mb: float = 19.0) -> None:
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True, check=True).stdout.strip())
    kbps = int(limit_mb * 8192 * 0.92 / dur)
    for crf in (20, 23, 26, 29):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=1440:-2:flags=lanczos", "-c:v", "libx264",
                        "-preset", "slow", "-crf", str(crf), "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
                        "-movflags", "+faststart", str(out)], check=True)
        if out.stat().st_size <= limit_mb * 1024 * 1024:
            return


def sheet(a: Path, b: Path, out: Path, n: int = 6, labels: tuple[str, str] = ("", "")) -> None:
    from PIL import Image, ImageDraw, ImageFont

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(a)],
                               capture_output=True, text=True, check=True).stdout.strip())
    w, h = 300, 534
    pad, top = 8, 56
    img = Image.new("RGB", (n * (w + pad) + pad, top + 2 * (h + pad) + 30), (14, 14, 16))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 26)
    tmp = out.parent / ".sheet_tmp.png"
    for i in range(n):
        t = dur * (i + 0.5) / n
        for row, src in enumerate((a, b)):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", str(tmp)], check=True)
            im = Image.open(tmp).convert("RGB").resize((w, h), Image.LANCZOS)
            img.paste(im, (pad + i * (w + pad), top + row * (h + pad)))
        d.text((pad + i * (w + pad) + 6, top + 2 * (h + pad) + 2), f"{t:.1f} с", font=f, fill=(200, 200, 200))
    d.text((pad, 14), f"верх: {labels[0]}   низ: {labels[1]}", font=f, fill=(235, 235, 235))
    tmp.unlink(missing_ok=True)
    img.save(out, quality=90)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--shots", required=True, help="номера планов: 4-8 или 4,5,7")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backends", default="blender,threejs")
    ap.add_argument("--quality", default=None)
    a = ap.parse_args()
    job = json.loads(Path(a.job).read_text("utf-8"))
    if a.quality:
        job["quality"] = a.quality
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shots = shots_arg(a.shots)
    res = {"job": a.job, "shots": shots, "quality": B.preset(job)["name"], "rows": []}
    for name in a.backends.split(","):
        mem_file = out / f"mem_{name}.json"
        mem_file.unlink(missing_ok=True)
        w = watch(mem_file)
        print(f"{name}: планы {shots}", flush=True)
        row = run_backend(job, name, shots, out)
        row["memory"] = unwatch(w, mem_file)
        res["rows"].append(row)
        print(json.dumps(row, ensure_ascii=False)[:600], flush=True)
    names = a.backends.split(",")
    if len(names) == 2:
        mp4s = [out / f"{n}.mp4" for n in names]
        labels = (LABEL.get(names[0], names[0]), LABEL.get(names[1], names[1]))
        side_by_side(mp4s[0], mp4s[1], out / "excerpt_compare.mp4", labels)
        small_copy(out / "excerpt_compare.mp4", out / "excerpt_compare_small.mp4")
        sheet(mp4s[0], mp4s[1], out / "excerpt_sheet.jpg", labels=labels)
        res["compare"] = {"mp4": str(out / "excerpt_compare.mp4"), "small": str(out / "excerpt_compare_small.mp4"),
                          "small_mb": round((out / "excerpt_compare_small.mp4").stat().st_size / 1048576, 1),
                          "sheet": str(out / "excerpt_sheet.jpg")}
    (out / "compare.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(res.get("compare"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
