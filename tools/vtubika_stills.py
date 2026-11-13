"""Кадры на оценку VRM-истории «Витубики» до рендера ролика: обложка (кадр 0) и по кадру на каждый план и реплику
другого голоса — с субтитрами, крючком и подписями реплик, как в готовом ролике, и лист кадров с номерами фраз.

    python tools/vtubika_stills.py tools/vtubika_plans/collab.json D:/vvf-offload/vtubika/story8_collab
        [--only 0,3a,7b] [--gl gpu] [--keep-room]

Шаги — те же, что у ``tools/vtubika_story.py --engine vrm`` до рендера (озвучка и слова из кэша ``OUT/work``, сцена,
спека VRM, комната 3D-пресета, бандл), потом ``render.mjs --preview`` на выбранные моменты, затем слой субтитров
(``burn_captions``) на каждый кадр отдельно: слова, крючок и реплики сдвигаются к началу однокадрового ролика.

Моменты: «0» — обложка (t = 0); «N» — середина плана N-й фразы (буквы a, b, c — планы фразы по порядку); «N+id» — реплика
другого голоса (середина её звучания). Файлы — ``OUT/stills/NN[x].jpg``, лист — ``OUT/stills_sheet.jpg``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import vtubika_story as VS  # noqa: E402


def moments(plan: dict, built: dict, lines: list[dict]) -> list[tuple[str, float, str]]:
    """(метка, время, что показать) — обложка, середина каждого плана (по фразе), середина каждой реплики."""
    out: list[tuple[str, float, str]] = [("0", 0.0, "обложка")]
    shots = built["shots"]
    for s in built["sentences"]:
        mine = [sh for sh in shots if sh.get("n") == s["n"] - 1]
        for k, sh in enumerate(mine):
            t0, t1 = float(sh["t0"]), float(sh["t1"])
            if k == 0 and s["n"] == 1:
                t0 = max(t0, 1.6)  # после обложки
            # Реплики в паузе фразы — свои кадры; план фразы — до первой реплики.
            inside = [ln for ln in lines if t0 < ln["t"] < t1]
            if inside:
                t1 = min(t1, inside[0]["t"])
            if t1 - t0 < 0.25:
                continue
            tag = f"{s['n']}{'abcdefgh'[k] if len(mine) > 1 else ''}"
            out.append((tag, round((t0 + t1) / 2 + min(0.6, (t1 - t0) * 0.15), 2), f"{sh['cam']}: {s['text'][:40]}"))
    for ln in lines:
        n = next((s["n"] for s in built["sentences"] if s["t0"] <= ln["t"] + 0.01 and
                  ln["t"] < (next((x["t0"] for x in built["sentences"] if x["n"] == s["n"] + 1), 1e9))), 0)
        out.append((f"{n}+{ln['id']}", round(ln["t"] + ln["dur"] * 0.55, 2), str(ln.get("caption") or ln["id"])))
    out.sort(key=lambda m: m[1])
    return out


def caption_still(plan: dict, words: list[dict], lines: list[dict], png: Path, t: float, out: Path, work: Path) -> None:
    """Слой субтитров на один кадр: короткий отрезок [t − окно, t] (кадр стоит весь отрезок), времена слов, крючка и
    реплик сдвинуты к его началу (отрицательные времена компоновщик не показывает), снимок — последний кадр."""
    window = min(t, 4.2)
    shift = t - window
    p = copy.deepcopy(plan)
    if p.get("hook"):
        until = float(p["hook"].get("until", 1.5))
        if t >= until:
            p.pop("hook")
        else:
            p["hook"]["until"] = until - shift
    shifted = [{**w, "start": round(w["start"] - shift, 3), "end": round(w["end"] - shift, 3)} for w in words
               if w["end"] - shift > -0.5]
    lns = [{**ln, "t": round(ln["t"] - shift, 3)} for ln in lines if ln["t"] - shift >= 0 and ln["t"] <= t + 0.05]
    dur = round(window + 0.1, 3)
    with tempfile.TemporaryDirectory(prefix="still-", dir=work) as tmp:
        clip = Path(tmp) / "f.mp4"
        VS._run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png), "-t", f"{dur}", "-r", "30", "-pix_fmt", "yuv420p",
                 "-vf", "scale=1080:1920", str(clip)], "кадр-ролик")
        capped = Path(tmp) / "c.mp4"
        VS.burn_captions(p, shifted, clip, capped, Path(tmp), dur, y=0.8, lines=lns)
        VS._run(["ffmpeg", "-y", "-v", "error", "-ss", f"{window:.3f}", "-i", str(capped), "-frames:v", "1", "-q:v", "2",
                 str(out)], "кадр")


def P_load(path: str) -> dict:
    from app import vtubika_plan as P

    return P.load_plan(path)


def sheet(plan: dict, out_dir: Path, made: list[dict]) -> None:
    """Лист кадров по времени с метками (номер фразы, время) и последним — концовка (кадр в конце карточки)."""
    from app import vtubika_endcard as E

    items = sorted(made, key=lambda m: m["t"])
    files = [Path(m["file"]) for m in items]
    labels = [f"{m['tag']}  {m['t']:.1f}s" for m in items]
    cfg = E.settings(plan)
    if cfg:
        card = E.render(cfg, out_dir / "work")
        still = out_dir / "stills" / "end_card.jpg"
        VS._run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, float(cfg['duration']) - 0.2):.2f}", "-i", str(card),
                 "-frames:v", "1", "-q:v", "2", str(still)], "кадр концовки")
        files.append(still)
        labels.append("конец")
    VS.tile(files, out_dir / "stills_sheet.jpg", 6, labels)


def prepare(a: argparse.Namespace) -> dict:
    """Те же шаги, что у истории до рендера: озвучка и слова (кэш), сцена, реплики, рот по голосам, спека VRM,
    комната 3D-пресета, бандл."""
    if Path(a.env).is_file():
        VS.load_env(Path(a.env))
    main_root = ROOT.parent.parent.parent if ".claude" in str(ROOT) else ROOT
    if "MOTION_PACKS_DIR" not in os.environ and not (ROOT / "vendor" / "packs").is_dir():
        os.environ["MOTION_PACKS_DIR"] = str(main_root / "vendor" / "packs")
    lib3d = Path(r"D:\vvf-data\runtime\models3d")
    if "SCENE3D_MODELS_DIR" not in os.environ and (lib3d / "manifest.json").is_file():
        os.environ["SCENE3D_MODELS_DIR"] = str(lib3d)
    from app.settings import settings

    hf = Path.home() / ".cache" / "huggingface" / "hub"
    if hf.is_dir():
        settings.whisper_model_dir = hf
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    from app import vtubika_plan as P
    from app import vtubika_room3d as ROOM
    from app import vtubika_voices as VOICES
    from app.vtubika_vrm import to_vrm_spec

    plan = P.load_plan(a.plan)
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"plan": str(Path(a.plan).resolve())}
    raw = VS.narrate(plan, work, report)
    narration, words = VS.timed_words(plan, raw, work, report)
    narration, words = VS.pace_beats(plan, narration, words, work, report)
    lead = float((plan.get("pace") or {}).get("lead", 0.0))
    words = [{**w, "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in words]
    total = round(float(words[-1]["end"]) + float(plan.get("tail") or 1.8), 3)
    report["lead"] = lead
    built = P.build(plan, words, total)
    lines = VS.extra_voices(plan, words, work, report)
    spec = to_vrm_spec(plan, built, words, total)
    report["lipsync"] = VOICES.attach(plan, spec, lines, narration, words, total, lead, work, VS.transcribe_words)
    plates = ROOM.apply(plan, spec, work, gl=a.gl, log=VS._log)
    spec_path = work / "scene_vrm_stills.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    subprocess.run(["npm", "run", "build:vrm"], cwd=ROOT, check=True, shell=sys.platform == "win32", stdin=subprocess.DEVNULL)
    return {"plan": plan, "built": built, "words": words, "lines": lines, "spec": spec, "plates": plates, "total": total,
            "report": report, "out_dir": out_dir, "work": work, "spec_path": spec_path, "narration": narration,
            "lead": lead}


def segment(a: argparse.Namespace, st: dict) -> int:
    """Отрезок A:B ролика: кадры VRM (тот же рендер), субтитры и подписи реплик, звук — из полного сведения
    (голос, реплики, музыка, звуки) с проверкой стыков звука по всему ролику; лента кадров рядом."""
    plan, built, words, lines, spec = st["plan"], st["built"], st["words"], st["lines"], st["spec"]
    work, total, report, lead = st["work"], st["total"], st["report"], st["lead"]
    a0, b0 = (float(x) for x in a.segment.split(":"))
    fps = int(spec.get("fps") or 30)
    out = Path(a.segment_out or st["out_dir"] / "segment.mp4")
    raw = work / "segment_raw.mp4"
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(st["spec_path"]), "--bundle",
              str(ROOT / "node" / "vrm" / "dist" / "vrm.js"), "--packs", a.vrm_packs, "--gl", a.gl, "--workers", "1",
              *(["--site", spec["siteDir"]] if spec.get("siteDir") else []),
              *(["--plates", str(st["plates"])] if st["plates"] else []),
              "--frames", f"{round(a0 * fps)}:{round(b0 * fps)}", "--out", str(raw)]
    r = subprocess.run(render, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f"рендер отрезка: {r.stderr.strip()[-1500:]}")
    rep = json.loads(r.stdout.strip().splitlines()[-1])
    # Полное сведение звука на пустой картинке (быстро) — проверка стыков по всему ролику, как у готового.
    blank = work / "blank.mp4"
    VS._run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=360x640:r={fps}:d={total}",
             "-c:v", "libx264", "-preset", "ultrafast", str(blank)], "пустая картинка")
    mixed = work / "mixed_audio_check.mp4"
    VS.mix(plan, blank, st["narration"], built["sfx"], total, mixed, work, report, lead, None, lines=lines)
    audio = VS.audio_check(st["narration"], mixed, lead, report, lines)
    # Субтитры отрезка: слова и реплики сдвинуты к его началу.
    dur = round(b0 - a0, 3)
    shifted = [{**w, "start": round(w["start"] - a0, 3), "end": round(w["end"] - a0, 3)} for w in words if w["end"] > a0 - 0.5]
    lns = [{**ln, "t": round(ln["t"] - a0, 3)} for ln in lines if a0 <= ln["t"] < b0]
    p = copy.deepcopy(plan)
    p.pop("hook", None)
    capped = work / "segment_caps.mp4"
    VS.burn_captions(p, shifted, raw, capped, work, dur, y=0.8, lines=lns)
    VS._run(["ffmpeg", "-y", "-v", "error", "-i", str(capped), "-ss", f"{a0:.3f}", "-t", f"{dur:.3f}", "-i", str(mixed),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], "отрезок со звуком")
    strip = out.with_name(out.stem + "_strip.jpg")
    VS._run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf",
             "fps=5,scale=270:480,drawtext=text='%{pts\\:hms}':x=6:y=6:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6,tile=10x5",
             "-frames:v", "1", "-q:v", "3", str(strip)], "лента кадров")
    info = {"segment": [a0, b0], "video": str(out), "strip": str(strip), "render": {k: rep.get(k) for k in ("ok", "frames", "presence", "warnings")},
            "audio_check": audio, "lipsync": report.get("lipsync"),
            "lines": [{k: v for k, v in ln.items() if k != "path"} for ln in lines if a0 <= ln["t"] < b0]}
    out.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0 if audio.get("ok") else 4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plan")
    ap.add_argument("out_dir")
    ap.add_argument("--only", help="метки кадров через запятую (0, 3a, 6+kat1)")
    ap.add_argument("--gl", default=os.environ.get("SCENE3D_GL", "gpu"))
    ap.add_argument("--env", default=str(ROOT.parent.parent.parent / ".env") if ".claude" in str(ROOT) else str(ROOT / ".env"))
    ap.add_argument("--vrm-packs", default=r"D:\vvf-offload\vtubika\vrm\vendor")
    ap.add_argument("--extra", help="ещё моменты: метка=секунды через запятую (7x=41.2)")
    ap.add_argument("--sheet-only", action="store_true", help="только собрать лист из OUT/stills.json (и концовки)")
    ap.add_argument("--segment", help="вместо кадров — отрезок ролика A:B секунд со звуком и субтитрами (проверка рта)")
    ap.add_argument("--segment-out", help="куда положить отрезок (mp4); рядом — лента кадров *_strip.jpg")
    a = ap.parse_args(argv)
    if a.sheet_only:
        out_dir = Path(a.out_dir).resolve()
        info = json.loads((out_dir / "stills.json").read_text("utf-8"))
        sheet(P_load(a.plan), out_dir, info["stills"])
        return 0
    st = prepare(a)
    plan, built, words, lines, spec, plates, total, report = (st[k] for k in ("plan", "built", "words", "lines", "spec",
                                                                               "plates", "total", "report"))
    out_dir, work, spec_path = st["out_dir"], st["work"], st["spec_path"]
    if a.segment:
        return segment(a, st)
    marks = moments(plan, built, lines)
    for kv in (a.extra or "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            marks.append((k.strip(), float(v), "доп."))
    marks.sort(key=lambda m: m[1])
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        marks = [m for m in marks if m[0] in want]
    prev_dir = work / "stills_raw"
    shutil.rmtree(prev_dir, ignore_errors=True)
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_path), "--bundle",
              str(ROOT / "node" / "vrm" / "dist" / "vrm.js"), "--packs", a.vrm_packs, "--gl", a.gl, "--workers", "1",
              *(["--site", spec["siteDir"]] if spec.get("siteDir") else []), *(["--plates", str(plates)] if plates else []),
              "--preview", ",".join(f"{t:.2f}" for _, t, _ in marks), "--preview-dir", str(prev_dir)]
    r = subprocess.run(render, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f"превью VRM: {r.stderr.strip()[-1500:]}")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    files = [Path(p["file"] if isinstance(p, dict) else p) for p in res["previews"]]
    stills = out_dir / "stills"
    stills.mkdir(exist_ok=True)
    made = []
    for (tag, t, what), png in zip(marks, files):
        name = tag.replace("+", "_")
        dst = stills / f"{name}.jpg"
        caption_still(plan, words, lines, png, t, dst, work)
        made.append({"tag": tag, "t": t, "what": what, "file": str(dst)})
    if not a.only:
        sheet(plan, out_dir, made)
    info = {"total": total, "stills": made, "lines": [{k: v for k, v in ln.items() if k != "path"} for ln in lines],
            "sentences": [{k: s[k] for k in ("n", "t0", "t1")} for s in built["sentences"]], "tts": report.get("tts"),
            "voices_extra": report.get("voices_extra"), "warn": report.get("voices_extra_warn"), "render": res.get("warnings")}
    (out_dir / "stills.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
