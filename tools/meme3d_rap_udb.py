"""Рэп-клип «Уроню БД» (песня Suno владельца, 1:50) — 3D toon-клип от лица Клода с Кодексом на цепи.

    python tools/meme3d_rap_udb.py chars D:/vvf-offload/memes/claude_rap          # этап 1: образы и позы, лист
    python tools/meme3d_rap_udb.py animatic D:/vvf-offload/memes/claude_rap       # этап 2: аниматик всей песни
    python tools/meme3d_rap_udb.py section D:/vvf-offload/memes/claude_rap --name chorus1
    python tools/meme3d_rap_udb.py final D:/vvf-offload/memes/claude_rap

Мир и герои — tools/meme3d_rap_world.py, планы по песне — tools/meme3d_rap_shots.py; время слов — выравнивание
текста по вокалу (app/lyrics_audio.py, demucs) в ``work/lyrics.json``, удары бочки/808 — ``work/beats.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_pilot as P1  # noqa: E402
import meme3d_rap_locs as Lc  # noqa: E402
import meme3d_rap_world as Wd  # noqa: E402

FY = Wd.FY
MODELS = Path("D:/vvf-data/runtime/models3d")


def render_frames(spec: dict, times: list[float], work: Path, name: str, gl: str = "gpu") -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    sp = work / f"{name}.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    prev = work / f"{name}_frames"
    shutil.rmtree(prev, ignore_errors=True)
    P1.wait_ram(5.0)
    P1._run(["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS),
             "--gl", gl, "--workers", "1", "--models", str(MODELS), "--preview", ",".join(f"{t:.2f}" for t in times), "--preview-dir", str(prev)], name)
    return [prev / (f"frame_{t:.2f}".replace(".", "_") + ".png") for t in times]


def sheet(files: list[Path], labels: list[str], out: Path, cols: int = 4, w: int = 540) -> None:
    h = w * 16 // 9
    inputs: list[str] = []
    chains = []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        lab = labels[i].replace(":", "\\:").replace("'", "").replace(",", "\\,")
        chains.append(f"[{i}:v]scale={w}:{h},drawtext=text='{lab}':x=14:y=14:fontsize=30:fontcolor=white:box=1:"
                      f"boxcolor=black@0.6:boxborderw=8:fontfile='C\\:/Windows/Fonts/arialbd.ttf'[v{i}]")
    n = len(files)
    layout = "|".join(f"{(i % cols) * w}_{(i // cols) * h}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=black[o]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-frames:v", "1", "-q:v", "2", str(out)],
                   check=True)


# --------------------------------------------------------------------------- этап 1: локации


def cam_key(t: float, pos: list[float], tgt: list[float], fov: float, *, roll: float = 0.0, fish: float = 0.0, cut: bool = True) -> dict:
    return {"t": round(t, 3), "position": [round(v, 4) for v in pos], "target": [round(v, 4) for v in tgt], "fov": fov, "roll": roll,
            "fisheye": fish, "flash": 0, "cut": cut}


def hold_shot(t0: float, t1: float, pos, tgt, fov, **kw) -> list[dict]:
    a = cam_key(t0, pos, tgt, fov, **kw)
    return [a, {**a, "t": round(t1 - 0.001, 3), "cut": False}]


def frame_cam(tgt: list[float], width_m: float, fov: float, az: float = 0.0, el: float = 5.0) -> list[float]:
    """Камера на цель: по горизонтали в кадр входит ``width_m`` м (кадр 9:16), азимут (° от +z) и угол сверху (°)."""
    import math

    vspan = width_m * 16 / 9
    d = (vspan / 2) / math.tan(math.radians(fov / 2))
    a, e = math.radians(az), math.radians(el)
    return [tgt[0] + d * math.cos(e) * math.sin(a), max(0.025, tgt[1] + d * math.sin(e)), tgt[2] + d * math.cos(e) * math.cos(a)]


BASE_C = {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "deflate": 0, "armL": "rest", "armR": "rest", "shades": 1}
BASE_K = {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "armL": "rest", "armR": "rest"}


def still_spec(loc_name: str, *, c_pos, c_yaw=0.0, c_pose=None, c_face="smug", c_look=(0, 0), k_pos=None, k_yaw=10.0, k_pose=None,
               k_face="angry", leash_len=0.44, cam=None, dof=0.0, extra=None, focus=None) -> dict:
    """Один кадр локации (t = 0.5): герои в позе, камера ``cam`` = (цель, ширина м, fov, азимут, угол сверху, фишай, крен)
    или готовые (pos, tgt, fov, fish, roll)."""
    loc = Lc.LOCATIONS[loc_name]()
    chars = [Wd.claude([{"t": 0, "ease": "step", **BASE_C, **(c_pose or {})}], [{"t": 0, "expr": c_face, "blend": 0, "look": list(c_look)}],
                       move=[{"t": 0, "position": c_pos, "ease": "step"}], yaw=c_yaw)]
    ex = list(extra or [])
    if k_pos is not None:
        chars.append(Wd.codex([{"t": 0, "ease": "step", **BASE_K, **(k_pose or {})}], [{"t": 0, "expr": k_face, "look": [0.1, 0]}],
                              move=[{"t": 0, "position": k_pos, "ease": "step"}], yaw=k_yaw))
        if leash_len:
            ex.append(Wd.leash(leash_len))
    if len(cam) == 7:
        tgt, wm, fov, az, el, fish, roll = cam
        pos = frame_cam(tgt, wm, fov, az, el)
    else:
        pos, tgt, fov, fish, roll = cam
    k = cam_key(0, pos, tgt, fov, fish=fish, roll=roll)
    k["dof"] = dof
    if focus:
        k["focus"] = focus
    sp = Wd.spec(loc, chars, ex, [k, {**k, "t": 0.999, "cut": False}], 1.0)
    for c in sp["characters"]:
        c["autoBlink"] = False
    return sp


def location_stills() -> list[tuple[str, dict]]:
    S = Lc.loc_skyline()["spot"]
    th = S["throne"]
    fy = FY
    return [
        ("1. открытие: кран над городом", still_spec(
            "skyline", c_pos=th, k_pos=[-0.3, 0, -0.2], k_face="angry", cam=([4.5, 3.6, 7.5], [-0.6, -1.6, -7.0], 50, 0.0, 0))),
        ("2. открытие: Клод на троне", still_spec(
            "skyline", c_pos=th, c_pose={"armL": "hips", "armR": [-10, 25, 0.3, 0], "lean": -4}, c_face="smug",
            k_pos=[-0.3, 0, -0.15], k_pose={"lean": 18, "turn": 20}, k_face="snarl", leash_len=0.5,
            cam=([-0.08, th[1] + fy + 0.05, -0.3], 0.62, 34, -15, -6, 0.0, 0), dof=1.6)),
        ("3. сцена с толпой", still_spec(
            "stage", c_pos=[0, 0.18, 0], c_pose={"lean": -6, "armL": [70, -10, 0.6, 0], "armR": [70, -10, 0.6, 0], "squash": 1.06}, c_face="happy",
            k_pos=[-0.34, 0.18, 0.12], k_pose={"lean": 15, "turn": 30, "hop": 0.01}, k_face="bark",
            cam=([0.0, 0.42, 0.3], 1.5, 52, 4, 3, 0.25, 0))),
        ("4. пентхаус, дождь жетонов", still_spec(
            "penthouse", c_pos=[0, 0, 0.3], c_pose={"lean": 8, "armL": [55, 40, 0.6, 0.4], "armR": [55, 40, 0.6, 0.4], "shades": 0}, c_face="happy",
            k_pos=[-0.34, 0, 0.4], k_pose={"turn": 30}, k_face="angry", cam=([0.0, 0.3, 0.0], 1.1, 50, -14, 4, 0.2, 0), dof=0.6)),
        ("5. улица, доставка", still_spec(
            "street", c_pos=[0.05, 0, 0.35], c_yaw=-10, c_pose={"armL": "hips", "armR": "point", "turn": -10}, c_face="smug",
            k_pos=[-0.3, 0, 0.45], k_face="angry", cam=([-0.2, 0.45, 0.4], 1.8, 50, 28, -2, 0.0, 0), dof=0.3)),
        ("6. класс", still_spec(
            "classroom", c_pos=[0.3, 0, -0.85], c_yaw=-25, c_pose={"armR": [40, 60, 0.7, 0.3], "lean": 6}, c_face="focused",
            cam=([0.0, 0.42, -0.6], 1.7, 48, 30, 8, 0.0, 0), dof=0.3)),
        ("7. горящий дата-центр", still_spec(
            "inferno", c_pos=[0, 0, 0.2], c_pose={"lean": -8, "armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, c_face="smug",
            k_pos=[-0.34, 0, 0.32], k_pose={"lean": 20, "turn": 25}, k_face="bark", cam=([0.0, 0.4, -0.2], 1.4, 56, 0, 0, 0.3, 0))),
    ]


def stage_locations(out: Path, gl: str) -> Path:
    files, labels = [], []
    for i, (lab, sp) in enumerate(location_stills()):
        files += render_frames(sp, [0.5], out / "work", f"loc{i}", gl)
        labels.append(lab)
    dst = out / "stage1b_locations.jpg"
    sheet(files, labels, dst, cols=4, w=540)
    return dst


# --------------------------------------------------------------------------- аниматик, участки, финал

SONG = Path(r"D:\vvf-offload\memes\claude_rap\song_final.mp3")
FPS = 30
MASK = {"разъебу": "разъ**у"}  # мат в подписях — только самое грубое, звук без цензуры


def runs(shots: list, t_from: float = 0.0, t_to: float = 1e9) -> list[tuple[str, int, int, list]]:
    """Подряд идущие планы одной локации → участки кадров [A, B) песни."""
    out: list[tuple[str, int, int, list]] = []
    for s in shots:
        if s.t1 <= t_from or s.t0 >= t_to:
            continue
        a, b = max(s.t0, t_from), min(s.t1, t_to)
        fa, fb = round(a * FPS), round(b * FPS)
        if fb <= fa:
            continue
        if out and out[-1][0] == s.loc and out[-1][2] == fa:
            out[-1] = (s.loc, out[-1][1], fb, out[-1][3] + [s])
        else:
            out.append((s.loc, fa, fb, [s]))
    return out


def render_runs(specs: dict, rr: list, work: Path, gl: str, tag: str) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    spec_files = {}
    for name, sp in specs.items():
        f = work / f"{tag}_{name}.json"
        f.write_text(json.dumps(sp, ensure_ascii=False), "utf-8")
        spec_files[name] = f
    clips = []
    for i, (loc, fa, fb, _) in enumerate(rr):
        dst = work / f"{tag}_{i:03d}_{loc}_{fa}_{fb}.mp4"
        clips.append(dst)
        if dst.exists() and dst.stat().st_size > 1000:
            continue
        P1.wait_ram(5.0)
        print(f"[rap] {tag} {i + 1}/{len(rr)} {loc} кадры {fa}:{fb}", file=sys.stderr, flush=True)
        proc = subprocess.run(["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_files[loc]), "--packs", str(P1.PACKS),
                               "--models", str(MODELS), "--gl", gl, "--workers", "2", "--frames", f"{fa}:{fb}", "--out", str(dst), "--crf", "20"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=3600)
        if proc.returncode == 3 and dst.exists():
            # Тёмные кадры — не повод бросать весь рендер: в лог, лист покажет.
            print(f"[rap] ВНИМАНИЕ тёмные кадры {loc} {fa}:{fb}: {proc.stdout.strip()[:300]}", file=sys.stderr, flush=True)
        elif proc.returncode != 0:
            raise RuntimeError(f"{tag} {loc} {fa}:{fb}: {proc.stderr.strip()[-1500:]}")
    return clips


def concat(clips: list[Path], dst: Path, t0: float, t1: float, size: tuple[int, int]) -> Path:
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), "utf-8")
    P1._run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(SONG),
             "-map", "0:v", "-map", "1:a", "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1,fps={FPS}", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dst)], "склейка")
    return dst


def lyric_words(t0: float = 0.0, t1: float = 1e9) -> list[dict]:
    import meme3d_rap_shots as Sh

    data = json.loads((Sh.DATA / "lyrics.json").read_text("utf-8"))
    out = []
    for ln in data["lines"]:
        for w in ln["words"]:
            if not (t0 - 0.01 <= w["t0"] < t1):
                continue
            txt = w["w"].strip("—–-,.:;!?«»\"' ")
            if not txt:
                continue
            low = txt.lower()
            for k, v in MASK.items():
                if low == k:
                    txt = v
            out.append({"t0": round(w["t0"] - t0, 3), "t1": round(min(w["t1"], t1) - t0, 3), "w": txt})
    # Слово держится до следующего (без мигания пустоты внутри строки).
    for a, b in zip(out, out[1:]):
        if 0 < b["t0"] - a["t1"] < 0.35:
            a["t1"] = b["t0"]
    return out


def add_captions(video: Path, words: list[dict], out: Path, work: Path, dur: float, title: str | None = None) -> Path:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers = [{"id": "caps", "type": "captions", "start": 0.0, "end": round(dur, 3), "x": 0.5, "y": 0.74,
               "props": {"style": "mrbeast", "size": 118, "activeColor": "#FFD23F", "uppercase": True, "maxWords": 2, "maxLines": 1,
                         "stroke": {"color": "#1a0d18", "width": 14}}}]
    if title:
        layers.append({"id": "title", "type": "text", "start": 0.0, "end": 4.2, "x": 0.5, "y": 0.13,
                       "props": {"text": title, "font": "Montserrat", "weight": 900, "size": 120, "color": "#fff6e8", "align": "center", "uppercase": True,
                                 "stroke": {"color": "#2b1030", "width": 14}, "bg": {"color": "#ff3fa4", "opacity": 1, "radius": 30, "padX": 40, "padY": 20}},
                       "out": {"type": "fade", "duration": 0.3}})
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=dur, layers=layers, camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-1200:]}")
    return out


def tg_copy(src: Path, dst: Path, dur: float, mb: float = 18.5) -> Path:
    """Копия для Telegram: два прохода x264 под размер ≤ 19 МБ."""
    kbps = max(400, int((mb * 8 * 1024) / dur) - 140)
    log = dst.with_suffix("")
    base = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=720:1280:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
            "-b:v", f"{kbps}k", "-pix_fmt", "yuv420p", "-passlogfile", str(log)]
    P1._run([*base, "-pass", "1", "-an", "-f", "mp4", "NUL" if sys.platform == "win32" else "/dev/null"], "tg 1")
    P1._run([*base, "-pass", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)], "tg 2")
    for f in dst.parent.glob(log.name + "*.log*"):
        f.unlink(missing_ok=True)
    return dst


def contact(video: Path, shots: list, out: Path, t0: float = 0.0) -> Path:
    times = [((s.t0 + s.t1) / 2) - t0 for s in shots]
    labels = [f"{s.t0:.1f} {s.lab}"[:34] for s in shots]
    cols = 8
    P1.frame_sheet(video, times, labels, out, cols=cols)
    return out


def credits(specs: dict, out: Path) -> Path:
    """Строки атрибуции моделей клипа (CC BY — обязательны в описании ролика; CC0 — для порядка)."""
    man = json.loads((MODELS / "manifest.json").read_text("utf-8"))["models"]
    used = sorted({o["model"] for sp in specs.values() for o in sp["objects"] if o.get("model") in man})
    by = [man[m]["attribution"] for m in used if man[m]["license"].startswith("CC-BY")]
    cc0 = sorted({f"{man[m]['author']}" for m in used if not man[m]["license"].startswith("CC-BY")})
    txt = ("3D-модели (CC BY 3.0):\n" + "\n".join(f"— {a}" for a in by) + "\n\nCC0: " + ", ".join(cc0) +
           " (Kenney, Quaternius, iPoly3D и др.; источники — tools/models3d/sources.json)\n")
    out.write_text(txt, "utf-8")
    return out


def stage_render(out: Path, gl: str, name: str, t0: float, t1: float, size: tuple[int, int], title: str | None = None) -> tuple[Path, Path]:
    import meme3d_rap_shots as Sh

    specs, shots = Sh.build_specs(*size)
    for a, b in zip(shots, shots[1:]):
        a.t1 = b.t0  # без дыр между планами
    shots[-1].t1 = Sh.DUR
    rr = runs(shots, t0, t1)
    work = out / "work" / name
    credits(specs, out / "credits.txt")
    clips = render_runs(specs, rr, work, gl, name)
    raw = concat(clips, work / f"{name}_raw.mp4", t0, t1, (1080, 1920))
    final = add_captions(raw, lyric_words(t0, t1), out / f"{name}.mp4", work, t1 - t0, title)
    sel = [s for s in shots if s.t1 > t0 and s.t0 < t1]
    sheet_ = contact(final, sel, out / f"{name}_sheet.jpg", t0)
    return final, sheet_


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["locations", "animatic", "section", "final"])
    ap.add_argument("out_dir")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=109.6)
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    out = Path(a.out_dir).resolve()
    if not a.no_build:
        subprocess.run(["npm", "run", "build:scene3d"], cwd=ROOT, check=True, shell=sys.platform == "win32")
    if a.cmd == "locations":
        print(stage_locations(out, a.gl))
    elif a.cmd == "animatic":
        v, sh = stage_render(out, a.gl, a.name or "stage2_animatic", a.t0, a.t1, (540, 960), title="Уроню БД")
        print(v, sh, tg_copy(v, v.with_name(v.stem + "_tg.mp4"), a.t1 - a.t0))
    elif a.cmd in ("section", "final"):
        v, sh = stage_render(out, a.gl, a.name or f"section_{a.t0:.0f}_{a.t1:.0f}", a.t0, a.t1, (1080, 1920),
                             title="Уроню БД" if a.t0 == 0 else None)
        print(v, sh, tg_copy(v, v.with_name(v.stem + "_tg.mp4"), a.t1 - a.t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
