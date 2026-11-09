"""Серия соло-ДнД «Мира от зрителей» (проект 10) от сценария до готового mp4 — сейчас серия 0 «Вы попали в мир».

    python tools/viewer_ep.py [--out D:/vvf-offload/vtubika/psx/ep0] [--env ../.env] [--gl gpu]
        [--preview 1,5,12.5] [--roll-still]

1. Сценарий огонька (app/viewer_ep.ep0_script) → бесплатный Gemini TTS (голос WISP_VOICE, не Aoede), кэш по тексту.
2. Слова — Whisper (tools/viewer_turn.words_for) → время фраз → сдвиг на LEAD (помехи и туман до первой фразы).
3. Огибающая голоса по кадрам → огонёк «говорит» (свет, размер пламени).
4. Сцена — app/viewer_ep.build_ep0_spec (основа — пресет декорации street) → рендер node/scene3d/render.mjs с
   бандлом PS1 (проверка присутствия героини на каждом кадре — в странице).
5. Склейки — ffmpeg scdet: найденные должны совпасть с объявленными (планы + всплески помех).
6. Звук — tools/viewer_ep_audio.py: голос, гул, ветер, помехи, звуки паков → −14 LUFS.
7. Лист ``sheet.jpg`` (кадр на событие), крупные вырезки ``crops/``, ``script.md`` (реплики), ``report.json``.

``--preview`` — только стопкадры на эти секунды (после озвучки). ``--roll-still`` — бонус: стопкадр окна броска
d20 и окна героини (``roll_still.png``) — компоненты для следующих серий.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from viewer_turn import check_cuts, log, render, run, words_for  # noqa: E402


def narrate(text: str, work: Path, voice: dict) -> tuple[Path, dict]:
    """Озвучка бесплатным Gemini (кэш по тексту и голосу); платная — ошибка (0 ₽)."""
    from app.dubbing import get_tts_provider

    key = hashlib.sha1(json.dumps([text, voice], ensure_ascii=False).encode()).hexdigest()[:12]
    wav, meta = work / f"voice_{key}.wav", work / f"voice_{key}.json"
    if wav.is_file() and meta.is_file():
        return wav, json.loads(meta.read_text("utf-8"))
    tts = get_tts_provider("gemini", voice=voice["voice"], style=voice["style"])
    speech = tts.synthesize(text, wav)
    info = {"provider": "gemini", "voice": voice["voice"], "model": tts.model, "chars": len(text),
            "cost_rub": float(speech.cost_rub or 0.0)}
    if info["cost_rub"]:
        raise SystemExit("озвучка оказалась платной — так нельзя (0 ₽)")
    meta.write_text(json.dumps(info, ensure_ascii=False), "utf-8")
    return wav, info


def use_cached_whisper() -> None:
    """Модель Whisper уже лежит в общем кэше huggingface — не качать её заново в data/ рабочей копии."""
    from app.settings import settings

    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if not any(settings.whisper_model_dir.glob("models--*/snapshots/*/model.bin")) and any(hub.glob("models--*/snapshots/*/model.bin")):
        settings.whisper_model_dir = hub


def chunk_bounds(frames: int, cuts: list[int], longest: int = 420) -> list[tuple[int, int]]:
    """Куски рендера: границы — на склейках планов (пружины волос в начале куска прогреваются заново — на склейке
    это не видно), кусок не длиннее longest кадров (иначе режется посередине)."""
    marks = sorted({c for c in cuts if 0 < c < frames})
    out, a = [], 0
    for c in marks + [frames]:
        while c - a > longest:
            out.append((a, a + longest))
            a += longest
        if c - a >= 30 or c == frames:
            out.append((a, c))
            a = c
    return out


def render_chunks(spec_path: Path, out: Path, gl: str, frames: int, work: Path, cuts: list[int]) -> dict:
    """Рендер кусками (chunk_bounds; готовые куски не переснимаются — продолжение после сбоя), склейка без
    перекодирования. Один браузер за раз (память машины)."""
    seg_dir = work / "chunks"
    seg_dir.mkdir(exist_ok=True)
    segs, black, pres = [], [], {"frames": 0, "missing": 0}
    key = hashlib.sha1(spec_path.read_bytes() + (ROOT / "node/vrm/dist/psx.js").read_bytes()).hexdigest()[:8]
    for a, b in chunk_bounds(frames, cuts):
        seg = seg_dir / f"seg_{key}_{a:05d}_{b:05d}.mp4"
        meta = seg.with_suffix(".json")
        if not (seg.is_file() and meta.is_file()):
            for attempt in (1, 2):
                proc = subprocess.run(["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(spec_path), "--bundle",
                                       str(ROOT / "node/vrm/dist/psx.js"), "--packs", "D:/vvf-offload/vtubika/vrm/vendor",
                                       "--gl", gl, "--workers", "1", "--format", "png", "--crf", "14", "--frames", f"{a}:{b}",
                                       "--out", str(seg)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      stdin=subprocess.DEVNULL, timeout=900)
                if proc.returncode == 0:
                    meta.write_text(proc.stdout.strip().splitlines()[-1], "utf-8")
                    break
                log(f"кусок {a}-{b}, попытка {attempt}: {(proc.stderr or proc.stdout).strip()[-300:]}")
                if proc.returncode in (3, 4) or attempt == 2:  # чёрные кадры / героиня пропала — не сбой машины
                    raise SystemExit(f"рендер куска {a}-{b}: {(proc.stderr or proc.stdout).strip()[-800:]}")
            log(f"кусок {a}-{b} готов")
        r = json.loads(meta.read_text("utf-8"))
        black += r.get("black_frames", [])  # номера кадров — от начала ролика
        p = r.get("presence") or {}
        pres["frames"] += int(p.get("frames", 0))
        pres["missing"] += int(p.get("missing", 0))
        segs.append(seg)
    lst = work / "chunks.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), "utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], "склейка кусков")
    return {"frames": frames, "black_frames": black, "presence": pres}


def isolated_jumps(diffs: list[float], fps: float, level: float = 14.0, ratio: float = 3.0) -> list[float]:
    """Склейки по разнице соседних кадров (уменьшенных до 34×60 — зерно плёнки усредняется): скачок выше level и
    в ratio раз выше медианы соседей (±4 кадра). Всплывающий интерфейс и мигание огонька дают 4–11 — ниже порога."""
    out = []
    for i, v in enumerate(diffs):
        near = sorted(diffs[max(0, i - 4):i] + diffs[i + 1:i + 5])
        med = near[len(near) // 2] if near else 0.0
        if v > level and v > ratio * max(med, 0.6):
            out.append(round((i + 1) / fps, 3))
    return out


def frame_cuts(mp4: Path, spec: dict) -> dict:
    """Проверка склеек, которая видит сквозь зерно (scdet на этой плёнке слеп: тёмные планы похожи по тону).
    Найденная, но не объявленная склейка — ошибка; объявленные планы, которые не нашлись, — в отчёт."""
    import numpy as np

    fps = float(spec.get("fps", 30))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vf", "scale=34:60:flags=area,format=gray", "-f",
                          "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 60, 34).astype(np.float32)
    diffs = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)).round(2).tolist()
    found = isolated_jumps(diffs, fps)
    declared = spec.get("cuts", [])
    bad = [t for t in found if not any(abs(t - d) <= 1.5 / fps for d in declared)]
    if bad:
        raise SystemExit(f"склейки не по плану (разница кадров): {bad}")
    shots = [round(s["t0"], 3) for s in spec["shots"][1:]]
    missing = [t for t in shots if not any(abs(t - x) <= 1.5 / fps for x in found)]
    return {"found": found, "shot_cuts_missing": missing}


def free_gb() -> float:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"], capture_output=True, text=True,
                             check=True, timeout=30).stdout
        return int(out.strip()) / 1024 / 1024
    except Exception:  # noqa: BLE001 - не Windows: считаем, что памяти хватает
        return 99.0


def words_aligned(text: str, wav: Path, work: Path) -> list[dict]:
    """Слова сценария на времени речи. Whisper large-v3-turbo (как у «Историй»), а если свободной памяти меньше
    3 ГБ — small: для привязки слов сценария к времени его хватает (текст известен заранее)."""
    from app.explainer_gen_words import heard_share, script_words

    cache = wav.with_suffix(".words.json")
    if cache.is_file():
        return json.loads(cache.read_text("utf-8"))
    if free_gb() >= 3.0:
        return words_for(text, wav)
    from app.settings import settings
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    class Decoded:  # звук декодирует ffmpeg (локальный PyAV бывает несовместим) — как tools/vtubika_story.py
        def __init__(self, model):
            self.model = model

        def transcribe(self, audio, **kw):
            import numpy as np

            if isinstance(audio, str):
                audio = np.frombuffer(np.array(voice_samples(Path(audio)), dtype=np.float32).tobytes(), dtype=np.float32)
            return self.model.transcribe(audio, **kw)

    def load(size: str):
        from faster_whisper import WhisperModel

        return Decoded(WhisperModel(size, device=settings.whisper_device, compute_type=settings.whisper_compute_type,
                                    download_root=str(settings.whisper_model_dir)))

    res = WhisperSubtitleProvider(model_loader=load).transcribe(wav, {"language": "ru"}, "small")
    heard = [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
             for w in res.words if str(w.word or "").strip()]
    share = heard_share(text, heard)
    if share < 0.7:
        raise SystemExit(f"в озвучке распознано {share:.0%} текста — не та речь")
    words = script_words(text, heard)
    cache.write_text(json.dumps(words, ensure_ascii=False), "utf-8")
    return words


def paced_voice(wav: Path, segs: list[dict], work: Path, speed: float = 1.0) -> Path:
    """Озвучка в темпе: куски фраз (app.viewer_ep.pace) на свои места, между ними тишина; затем atempo (speed)."""
    out = work / f"{wav.stem}_paced{speed:g}.wav"
    split = f"[0]asplit={len(segs)}" + "".join(f"[i{i}]" for i in range(len(segs)))
    chains = [split] + [f"[i{i}]atrim={s['src'][0]}:{s['src'][1]},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,"
              f"afade=t=out:st={max(0.0, s['src'][1] - s['src'][0] - 0.04):.3f}:d=0.04,adelay={int(s['at'] * 1000)}:all=1[a{i}]"
              for i, s in enumerate(segs)]
    graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(segs))) + \
        f"amix=inputs={len(segs)}:normalize=0" + (f",atempo={speed}" if speed != 1.0 else "") + "[o]"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-filter_complex", graph, "-map", "[o]", "-ac", "1", str(out)], "темп")
    return out


def voice_samples(wav: Path, rate: int = 16000) -> list[float]:
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
                         capture_output=True, check=True).stdout
    return (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0).tolist()


def audio_len(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], "ffprobe")
    return float(out.stdout.strip())


def frame_at(mp4: Path, t: float, out: Path, scale: str = "") -> Path:
    vf = ["-vf", scale] if scale else []
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", *vf,
         str(out)], "кадр")
    return out


def sheet_marks(ev: dict, total: float) -> list[tuple[float, str]]:
    return [(0.5, "помехи"), (2.7, "туман"), (ev["wake_cut"] - 0.6, "лежит"), (ev["up"] + 1.0, "садится"),
            (ev["stand"] + 0.8, "встаёт"), (ev["wisp"] + 0.6, "огонёк в тумане"), (ev["lore"] - 0.4, "подлетел"),
            (ev["lore"] + 2.0, "кружит"), (ev["viewer"] + 1.5, "зрителям"), (ev["rules"] + 1.5, "правила"),
            (ev["menu"] + 1.8, "меню: имя"), (ev["stats"] + 2.0, "статы"), (ev["items"] + 2.2, "предмет"),
            (ev["cta"] + 2.5, "призыв"), (ev["g1"] + 0.08, "тень"), (ev["g2"] + 0.05, "тень 2"),
            (min(total - 0.1, ev["static_end"] + 0.3), "помехи")]


def write_script(path: Path, script: list[dict], beats: list[dict], voice: dict, total: float) -> None:
    lines = ["# Серия 0 «Вы попали в мир» — реплики огонька", "",
             f"Голос: Gemini «{voice['voice']}» (бесплатно), подача: {voice['style']}.", f"Длина ролика: {total:.1f} с.", ""]
    for s, b in zip(script, beats):
        lines.append(f"- {b['t0']:5.1f}–{b['t1']:5.1f} с · {s['text']}")
    lines += ["", "Титр-призыв: " + " / ".join(x for x in __import__("app.viewer_ep", fromlist=["CTA_LINES"]).CTA_LINES if x)]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def roll_still(out_dir: Path, gl: str, work: Path) -> Path:
    """Бонус: стопкадр — героиня на улице, огонёк рядом, окно героини и бросок d20 (после приземления кубика)."""
    from app import viewer_dnd as D
    from app import viewer_ep as E

    base = E.scene_base("street")
    hero = D.new_heroine("Лиса", 2, 1)
    res = D.resolve(hero, "ЛОВКОСТЬ", "normal", "серия 1|пример броска", tag="")
    spec = {k: v for k, v in base.items() if k not in ("stills",)}
    spec.update({
        "duration": 4, "cuts": [], "look_at": [{"t": -5, "target": [0.6, 1.7, 0.6], "head": 0.6}],
        "shots": [{"name": "бросок", "t0": 0, "t1": 5, "minHeight": 0.12,
                   "camKeys": [{"t": 0, "yaw": 12, "dist": 3.0, "height": 1.45, "target": 1.15, "fov": 40}]}],
        "wisp": {**base["wisp"], "appear": -5, "keys": [{"t": 0, "pos": [0.65, 1.75, 0.5]}]},
        "stats": E.stats_spec(hero, 0), "roll": E.roll_spec(res, 0.2, "ПЕРЕЛЕЗТЬ ЗАБОР", y=300),
        "captions": [], "fx": {**base["fx"], "keys": [{"t": 0, "static": 0.03}]},
    })
    sp = work / "roll_spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    t = 0.2 + 0.3 + 1.4 + 1.3
    r = render(sp, out_dir, gl, f"{t}", work / "roll")
    src = Path(r["previews"][0]["file"])
    dst = out_dir / "roll_still.png"
    dst.write_bytes(src.read_bytes())
    (out_dir / "roll_still.json").write_text(json.dumps({"heroine": hero, "roll": res}, ensure_ascii=False, indent=1), "utf-8")
    return dst


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx/ep0"))
    ap.add_argument("--env", type=Path, default=ROOT.parent.parent.parent / ".env")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--preview", default="")
    ap.add_argument("--tag", default="", help="подкаталог стопкадров preview_<tag>")
    ap.add_argument("--roll-still", action="store_true")
    ap.add_argument("--skip-render", action="store_true", help="видео уже есть в work/silent.mp4 — только звук и лист")
    a = ap.parse_args(argv)
    work = a.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    if a.env.is_file():
        from vtubika_story import load_env

        load_env(a.env)
    os.environ.setdefault("MOTION_PACKS_DIR", "D:/vvf-offload/vtubika/packs_vtb")
    from app import viewer_ep as E

    run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    if a.roll_still:
        print(json.dumps({"roll_still": str(roll_still(a.out, a.gl, work))}, ensure_ascii=False))
        return 0
    script = E.ep0_script()
    text = " ".join(s["text"] for s in script)
    voice, tts = narrate(text, work, E.WISP_VOICE)
    log(f"озвучка: {tts}, {audio_len(voice):.1f} с")
    use_cached_whisper()
    words = words_aligned(text, voice, work)
    if words[0]["start"] > 2.0:
        raise SystemExit(f"речь начинается на {words[0]['start']:.1f} с — модель зачитала указание к голосу; "
                         f"удалите {voice.name} и поправьте style")
    segs, speed, beats = E.ep0_beats(script, words, E.LEAD)
    voice = paced_voice(voice, segs, work, speed)
    log(f"темп: ускорение ×{speed}")
    total = round(beats[-1]["t1"] + E.TAIL, 2)
    env = E.envelope(voice_samples(voice), 16000, 30, lead=E.LEAD)
    shifted = [{"word": w["word"], "start": w["start"], "end": w["end"]} for b in beats for w in b["words"]]
    spec = E.build_ep0_spec(E.scene_base("street"), beats, shifted, total, env)
    events = spec.pop("_events")
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    if a.preview:
        res = render(spec_path, a.out, a.gl, a.preview, work / (f"preview_{a.tag}" if a.tag else "preview"))
        print(json.dumps({"previews": [p["file"] for p in res.get("previews", [])], "events": events, "total": total},
                         ensure_ascii=False))
        return 0
    silent = work / "silent.mp4"
    res: dict = {}
    if not a.skip_render:
        fps = spec.get("fps", 30)
        shot_cuts = [int(round(s["t0"] * fps)) for s in spec["shots"][1:]]
        res = render_chunks(spec_path, silent, a.gl, int(round(total * fps)), work, shot_cuts)
        log(f"рендер: {res['frames']} кадров, чёрных {len(res['black_frames'])}")
        if res["black_frames"]:
            raise SystemExit(f"чёрные кадры: {res['black_frames'][:10]}")
    cuts = {"scdet": check_cuts(silent, spec), "frames": frame_cuts(silent, spec)}
    if cuts["frames"]["shot_cuts_missing"]:
        raise SystemExit(f"объявленные склейки планов не видны в кадрах: {cuts['frames']['shot_cuts_missing']}")
    from viewer_ep_audio import mix

    final = a.out / "ep0.mp4"
    glitch = [k["t"] for k in spec["fx"]["keys"] if k.get("static", 0) >= 0.45 and k["t"] > 2.9]
    mixed = mix(silent, voice, E.LEAD, total, events, spec["fx"]["keys"], sorted(set(round(g, 2) for g in glitch))[:4],
                final, work)
    from vtubika_story import tile

    marks = sheet_marks(events, total)
    frames = [frame_at(final, t, work / f"sheet_{i:02d}.png", "scale=360:640:flags=neighbor") for i, (t, _) in enumerate(marks)]
    tile(frames, a.out / "sheet.jpg", cols=6, labels=[f"{t:.1f} {lab}" for t, lab in marks])
    crops = a.out / "crops"
    crops.mkdir(exist_ok=True)
    for name, t, box in (("face", events["viewer"] + 1.5, "crop=540:540:270:420"),
                         ("menu", events["items"] + 2.2, "crop=1080:1100:0:100"),
                         ("wisp", events["lore"] + 2.0, "crop=720:720:180:500"),
                         ("shadow", events["g1"] + 0.08, "crop=1080:1080:0:420")):
        frame_at(final, t, crops / f"{name}.png", box)
    write_script(a.out / "script.md", script, beats, E.WISP_VOICE, total)
    report = {"final": str(final), "duration": total, "frames": res.get("frames"), "presence": res.get("presence"),
              "cuts": cuts, "tts": tts, "mix": mixed, "events": events, "text": text}
    (a.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("final", "duration", "frames", "cuts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
