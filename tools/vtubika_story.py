"""История «Витубики» от плана до готового mp4 на своём 3D-движке (без Blender, без нейросетей видео).

    python tools/vtubika_story.py tools/vtubika_plans/fox.json D:/vvf-offload/vtubika/story1 [--look cozy]
        [--gl gpu] [--env ../.env] [--preview 3,12,30] [--looks cozy,neon,pastel --segment 24:34] [--name story1]

Шаги (каждый кэшируется в OUT/work, повторный запуск берёт готовое):

1. **Озвучка** — бесплатный Gemini TTS (``app.dubbing.get_tts_provider("gemini")``), голос рассказчика вселенной
   (``app.vtubika.NARRATOR``, план может переопределить). Gemini не ответил — Polza TTS, только если в плане
   ``voice.fallback: "polza"``, с потолком ``POLZA_CAP_RUB`` за ночь (оценка до синтеза, факт — в отчёт).
2. **Слова** — Whisper (тот же, что у «Историй»: ``explainer_gen.transcribe_words``) → слова сценария на времени
   речи (``script_words``) → темп и паузы ``app.series3d_voice.fit_narration``.
3. **Сцена** — ``app.vtubika_plan.build``: кадры и события на словах плана.
4. **Рендер** — ``app.scene3d.render_scene`` (GPU, один браузер).
5. **Субтитры** — слой ``captions`` моушн-компоновщика стилем «Историй» (``app.stories_3d._captions``), ключевые
   слова цветом из плана.
6. **Звук** — голос + музыка-подложка (CC0, на ``under_db`` тише голоса) + звуки паков на событиях, сведение
   ``app.motion_render.build_audio_layers_args``, затем громкость −14 LUFS.
7. **Проверка** — лист кадров (12), кадр на каждую фразу и список «фраза → что в кадре» (check.md).

``--looks`` — тот же отрезок ``--segment`` в нескольких образах комнаты → ``looks_sheet.jpg`` (строка на образ).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

POLZA_CAP_RUB = 100.0
TARGET_LUFS = -14.0


def _log(msg: str) -> None:
    print(f"[vtubika] {msg}", file=sys.stderr, flush=True)


def load_env(path: Path) -> None:
    """Из .env — только ключи Gemini/Polza (и их прокси); остальное не трогаем."""
    for line in path.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and (key.startswith("GEMINI_") or key.startswith("POLZA_")) and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _run(args: list[str], what: str) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{what}: {proc.stderr.strip()[-600:]}")


# --------------------------------------------------------------------------- шаги


def narrate(plan: dict, work: Path, report: dict) -> Path:
    """Озвучка всей истории одним запросом (кэш по тексту и голосу)."""
    from app import vtubika as U
    from app import vtubika_plan as P
    from app.dubbing import get_tts_provider

    voice = {**U.NARRATOR, **(plan.get("voice") or {})}
    text = P.narration_text(plan)
    key = hashlib.sha1(json.dumps([text, voice], ensure_ascii=False).encode()).hexdigest()[:12]
    raw = work / f"narration_{key}.wav"
    meta = work / f"narration_{key}.json"
    if raw.is_file() and meta.is_file():
        report["tts"] = json.loads(meta.read_text("utf-8"))
        return raw
    info: dict = {"provider": voice["provider"], "voice": voice["voice"], "chars": len(text), "cost_rub": 0.0}
    if voice["provider"] == "polza":
        # Платный голос — только явным разрешением в плане и под потолком.
        if not voice.get("paid_ok"):
            raise RuntimeError("голос Polza платный: нужен \"paid_ok\": true в плане (по команде владельца)")
        paid = get_tts_provider("polza", model=voice.get("model"), voice=voice["voice"])
        if paid.price_rub_per_char() * len(text) > POLZA_CAP_RUB:
            raise RuntimeError(f"Polza TTS: оценка выше потолка {POLZA_CAP_RUB} ₽")
    try:
        tts = get_tts_provider(voice["provider"], model=voice.get("model"), voice=voice["voice"], style=voice.get("style"))
        speech = tts.synthesize(text, raw)
        info.update({"model": tts.model, "cost_rub": float(speech.cost_rub or 0.0), "usage": speech.raw_usage})
    except Exception as exc:  # noqa: BLE001 - Gemini недоступен: платный запасной — только если разрешён планом
        if voice.get("fallback") != "polza":
            raise
        tts = get_tts_provider("polza", voice=voice.get("polza_voice"))
        estimate = tts.price_rub_per_char() * len(text)
        if estimate > POLZA_CAP_RUB:
            raise RuntimeError(f"Polza TTS: оценка {estimate:.1f} ₽ выше потолка {POLZA_CAP_RUB} ₽") from exc
        speech = tts.synthesize(text, raw)
        info.update({"provider": "polza", "model": tts.model, "voice": tts.voice, "gemini_error": str(exc)[:200],
                     "estimate_rub": round(estimate, 2), "cost_rub": float(speech.cost_rub or estimate)})
    meta.write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    report["tts"] = info
    return raw


def transcribe_words(path: Path) -> list[dict]:
    """Слова озвучки с временем — тот же Whisper и те же настройки, что у «Историй» (WhisperSubtitleProvider,
    large-v3-turbo, русский); звук декодирует ffmpeg (16 кГц моно) — локальный PyAV бывает несовместим."""
    import numpy as np

    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.subtitles.whisper_local import WhisperSubtitleProvider
    from app.settings import settings

    class _Decoded:
        def __init__(self, model):
            self.model = model

        def transcribe(self, audio, **kw):
            if isinstance(audio, str):
                pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                     capture_output=True, check=True).stdout
                audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            return self.model.transcribe(audio, **kw)

    def load(size: str):
        from faster_whisper import WhisperModel

        return _Decoded(WhisperModel(size, device=settings.whisper_device, compute_type=settings.whisper_compute_type,
                                     download_root=str(settings.whisper_model_dir)))

    result = WhisperSubtitleProvider(model_loader=load).transcribe(Path(path), {"language": "ru"}, QUALITY_WHISPER_MODEL)
    return [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
            for w in result.words if str(w.word or "").strip()]


def timed_words(plan: dict, raw: Path, work: Path, report: dict) -> tuple[Path, list[dict]]:
    """Whisper → слова сценария → озвучка под темп (паузы короче). Кэш рядом с озвучкой."""
    from app import series3d_voice as VF
    from app import vtubika_plan as P
    from app.explainer_gen_words import heard_share, script_words

    pace = plan.get("pace") or {}
    cap = float(pace.get("tempo_cap", VF.MAX_TEMPO))
    cache = raw.with_name(f"{raw.stem}_t{cap:g}.words.json")
    fitted = raw.with_name(f"{raw.stem}_t{cap:g}_fit.mp3")
    if cache.is_file() and fitted.is_file():
        data = json.loads(cache.read_text("utf-8"))
        report["voice_fit"] = data["fit"]
        return fitted, data["words"]
    heard = transcribe_words(raw)
    text = P.caption_text(plan)
    share = heard_share(text, heard)
    if share < 0.6:
        raise RuntimeError(f"в озвучке распознано {share:.0%} текста — не та речь")
    words = script_words(text, heard)
    sentences = [{"text": s["text"]} for s in plan["sentences"]]
    fit = VF.fit_narration(raw, fitted, sentences, words, tempo_cap=cap)
    info = {k: fit[k] for k in ("tempo", "audio_sec", "source_sec", "wps_before", "wps_after", "speech_end")}
    info["heard_share"] = share
    cache.write_text(json.dumps({"words": fit["words"], "fit": info}, ensure_ascii=False), "utf-8")
    report["voice_fit"] = info
    return fitted, fit["words"]


def pace_beats(plan: dict, narration: Path, words: list[dict], work: Path, report: dict) -> tuple[Path, list[dict]]:
    """Паузы-«биты» после фраз (``pause_after`` фразы, с) и общий темп (``pace.tempo`` < 1 — медленнее): картинка
    успевает показать то, о чём сказали. Тишина вставляется в середину паузы между фразами; слова сдвигаются."""
    from app import series3d_voice as VF
    from app import vtubika_plan as P

    pauses = [float(s.get("pause_after") or 0.0) for s in plan["sentences"]]
    tempo = float((plan.get("pace") or {}).get("tempo", 1.0))
    if not any(pauses) and abs(tempo - 1.0) < 1e-3:
        return narration, words
    key = hashlib.sha1(json.dumps([str(narration), pauses, tempo]).encode()).hexdigest()[:10]
    out = work / f"narration_paced_{key}.mp3"
    cache = out.with_suffix(".json")
    if out.is_file() and cache.is_file():
        return out, json.loads(cache.read_text("utf-8"))
    import numpy as np

    pcm = VF.decode(narration)
    times = P.token_times(plan, words)
    cuts: list[tuple[float, float]] = []  # (точка разреза, сколько тишины)
    for n in range(len(times) - 1):
        if pauses[n] > 0:
            cuts.append(((times[n][-1]["end"] + times[n + 1][0]["start"]) / 2, pauses[n]))
    parts, prev = [], 0
    for t, gap in cuts:
        k = int(t * VF.SR)
        parts += [pcm[prev:k], np.zeros(int(gap * VF.SR), dtype=np.float32)]
        prev = k
    parts.append(pcm[prev:])
    raw = out.with_suffix(".wav")
    VF._write_wav(raw, np.concatenate(parts))
    graph = ["-filter:a", f"atempo={tempo:.3f}"] if abs(tempo - 1.0) > 1e-3 else []
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), *graph, "-c:a", "libmp3lame", "-b:a", "160k", str(out)], "темп")
    raw.unlink(missing_ok=True)

    def shift(t: float) -> float:
        return (t + sum(g for c, g in cuts if c <= t)) / tempo

    paced = [{**w, "start": round(shift(w["start"]), 3), "end": round(shift(w["end"]), 3)} for w in words]
    cache.write_text(json.dumps(paced, ensure_ascii=False), "utf-8")
    report["beats"] = {"pauses_sec": round(sum(pauses), 2), "tempo": tempo}
    return out, paced


def burn_captions(plan: dict, words: list[dict], video: Path, out: Path, work: Path, total: float) -> None:
    from app import vtubika_plan as P
    from app.motion_render_comp import build_compositor_args, write_comp
    from app.stories_3d import _captions
    from app.stories_library import DEFAULT_CAPTIONS

    props = _captions({"captions": dict(DEFAULT_CAPTIONS)})
    comp_words, marks = P.caption_words(words, plan.get("highlight") or {})
    props["highlightWords"] = marks
    layer = {"id": "caps", "type": "captions", "start": 0.0, "end": round(total, 3), "x": 0.5,
             "y": float(DEFAULT_CAPTIONS["y"]), "props": props}
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=30, duration=total, layers=[layer], camera=[],
                      words=comp_words)
    (work / "assets.json").write_text("{}", "utf-8")
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-600:]}")


def mix(plan: dict, video: Path, narration: Path, sfx: list[dict], total: float, out: Path, work: Path,
        report: dict, lead: float = 0.0) -> None:
    """Голос, музыка на under_db тише голоса, звуки паков → громкость −14 LUFS."""
    from app import vtubika as U
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_assets import resolve_pack_ref
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs

    music = {**U.MUSIC, **(plan.get("music") or {})}
    music_path = Path(music["file"]) if Path(music["file"]).is_absolute() else U.VTUBIKA_DIR / "music" / music["file"]
    voice_db = voice_lufs(narration)
    layers: list[dict] = [{"id": "voice", "type": "audio", "start": round(lead, 3), "end": round(total, 3),
                           "props": {"asset_id": 9001, "mode": "track", "volume": 1.0, "role": "voice"}}]
    assets = {asset_key(9001): str(narration)}
    notes: list[str] = []
    if music_path.is_file():
        m_lufs = float(ebur128(music_path)["i"])
        gain = round(voice_db - float(music["under_db"]) - m_lufs, 2)
        assets[asset_key(9002)] = str(music_path)
        layers.append({"id": "music", "type": "audio", "start": 0.0, "end": round(total, 3),
                       "props": {"asset_id": 9002, "mode": "track", "volume": 1.0, "gain_db": gain, "role": "music",
                                 "fadeIn": music.get("fade_in", 1.0), "fadeOut": music.get("fade_out", 2.5)}})
        report["music"] = {"file": music_path.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain,
                           "under_db": music["under_db"]}
    else:
        notes.append(f"нет музыки {music_path}")
    for n, s in enumerate(sfx):
        path = resolve_pack_ref(s["ref"])
        if path is None:
            notes.append(f"нет звука {s['ref']}")
            continue
        assets[asset_key(s["ref"])] = str(path)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": s["t"], "end": round(min(total, s["t"] + 2.5), 3),
                       "props": {"asset_id": s["ref"], "volume": s["volume"], "mode": "effect"}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, total, clip_has_audio=False,
                                   speech_db=speech_level_db(narration), notes=notes)
    _run(args, "сведение")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af",
          f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-movflags", "+faststart", str(out)], "громкость")
    report["mix_notes"] = notes
    report["sfx"] = len([x for x in layers if x["id"].startswith("sfx")])


def sentence_frames(video: Path, built: dict, out_dir: Path) -> dict[int, str]:
    """Кадр на каждую фразу (середина фразы) — для проверки «фраза → что в кадре»."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {}
    for s in built["sentences"]:
        t = (s["t0"] + s["t1"]) / 2
        path = out_dir / f"{s['n']:02d}.jpg"
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2",
              "-q:v", "3", str(path)], "кадр фразы")
        frames[s["n"]] = str(path)
    return frames


def tile(images: list[Path], out: Path, cols: int, labels: list[str] | None = None) -> None:
    """Сетка картинок одинакового размера (ffmpeg xstack), подписи сверху."""
    inputs: list[str] = []
    for p in images:
        inputs += ["-i", str(p)]
    n = len(images)
    if n == 1:
        _run(["ffmpeg", "-y", "-v", "error", *inputs, "-vf", "scale=360:640", "-frames:v", "1", "-q:v", "3", str(out)], "кадр")
        return
    rows = -(-n // cols)
    chains = []
    for i in range(n):
        label = (labels or [""] * n)[i].replace(":", "\\:").replace("'", "")
        draw = f",drawtext=text='{label}':x=10:y=10:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.6" if label else ""
        chains.append(f"[{i}:v]scale=360:640{draw}[v{i}]")
    layout = "|".join(f"{(i % cols) * 360}_{(i // cols) * 640}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + \
        f"xstack=inputs={n}:layout={layout}:fill=white[out]"
    _run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1",
          "-q:v", "3", str(out)], f"сетка {rows}×{cols}")


def render_segment(spec: dict, out: Path, frames: tuple[int, int], gl: str) -> dict:
    """Отрезок кадров [a, b) сцены → mp4 (тот же рендер, что у всей истории)."""
    from app import scene3d as S3

    S3.validate_spec(spec, check_assets=False)
    with __import__("tempfile").TemporaryDirectory(prefix="vtb-", dir=out.parent) as tmp:
        args = S3.build_args(S3._with_spec(spec, Path(tmp)), out=out, workers=1, gl=gl, chrome=S3.chromium_path(),
                             frames=frames)
        return S3._result(S3._run(args, S3.TIMEOUT_SEC))


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="история Витубики: план → mp4")
    ap.add_argument("plan")
    ap.add_argument("out_dir")
    ap.add_argument("--name", default="story1")
    ap.add_argument("--sheet", default="sheet.jpg", help="имя листа кадров (12 кадров)")
    ap.add_argument("--look")
    ap.add_argument("--gl", default=os.environ.get("SCENE3D_GL", "gpu"), choices=["gpu", "swiftshader"])
    ap.add_argument("--env", default=str(ROOT.parent.parent.parent / ".env") if ".claude" in str(ROOT) else str(ROOT / ".env"))
    ap.add_argument("--preview", help="только кадры PNG сцены: секунды через запятую")
    ap.add_argument("--looks", help="образы для сравнения отрезка: cozy,neon,pastel")
    ap.add_argument("--segment", default="24:34", help="отрезок для --looks, секунды A:B")
    ap.add_argument("--skip-render", action="store_true", help="только озвучка, слова и сцена (scene.json)")
    args = ap.parse_args(argv)
    if Path(args.env).is_file():
        load_env(Path(args.env))
    main_root = ROOT.parent.parent.parent if ".claude" in str(ROOT) else ROOT
    if "MOTION_PACKS_DIR" not in os.environ and not (ROOT / "vendor" / "packs").is_dir():
        os.environ["MOTION_PACKS_DIR"] = str(main_root / "vendor" / "packs")  # worktree: паки звуков — из основной копии
    from app.settings import settings

    hf = Path.home() / ".cache" / "huggingface" / "hub"
    if hf.is_dir():
        settings.whisper_model_dir = hf  # Whisper — из локального кэша моделей
    from app import scene3d as S3
    from app import vtubika_plan as P

    plan = P.load_plan(args.plan)
    out_dir = Path(args.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"plan": str(Path(args.plan).resolve())}
    started = time.time()
    raw = narrate(plan, work, report)
    narration, words = timed_words(plan, raw, work, report)
    narration, words = pace_beats(plan, narration, words, work, report)
    # Вступление: общий план и музыка до первого слова — голос и слова сдвигаются на lead.
    lead = float((plan.get("pace") or {}).get("lead", 0.0))
    words = [{**w, "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in words]
    report["lead"] = lead
    speech_end = float(words[-1]["end"])
    total = round(speech_end + float(plan.get("tail") or 1.8), 3)
    built = P.build(plan, words, total, look=args.look)
    (work / "scene.json").write_text(json.dumps(built["spec"], ensure_ascii=False), "utf-8")
    report.update({"duration": total, "speech_end": speech_end, "look": built["look"], "shots": len(built["shots"])})
    if args.preview:
        # «mids» — середина каждой фразы (проверка «фраза → что в кадре» без полного рендера).
        times = ([round((s["t0"] + s["t1"]) / 2, 2) for s in built["sentences"]] if args.preview == "mids"
                 else [float(x) for x in args.preview.split(",")])
        prev_dir = work / "preview"
        shutil.rmtree(prev_dir, ignore_errors=True)
        res = S3.preview_frames(built["spec"], times, prev_dir, gl=args.gl, check_assets=False)
        report["preview"] = [r["file"] for r in res]
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    if args.looks:
        a, b = (float(x) for x in args.segment.split(":"))
        rows: list[Path] = []
        labels: list[str] = []
        report["looks"] = {}
        for lk in [x.strip() for x in args.looks.split(",") if x.strip()]:
            spec = P.build(plan, words, total, look=lk)["spec"]
            seg = out_dir / f"look_{lk}.mp4"
            t1 = time.time()
            render_segment(spec, seg, (int(a * 30), int(b * 30)), args.gl)
            report["looks"][lk] = {"video": str(seg), "render_sec": round(time.time() - t1, 1)}
            for k in range(4):
                fr = work / f"look_{lk}_{k}.jpg"
                _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{(b - a) * (k + 0.5) / 4:.3f}", "-i", str(seg), "-frames:v", "1",
                      "-q:v", "3", str(fr)], "кадр образа")
                rows.append(fr)
                labels.append(f"{lk} {a + (b - a) * (k + 0.5) / 4:.1f}s")
        tile(rows, out_dir / "looks_sheet.jpg", 4, labels)
        report["looks_sheet"] = str(out_dir / "looks_sheet.jpg")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    if args.skip_render:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    t3d = time.time()
    raw_video = work / "scene.mp4"
    # Кэш 3D: та же сцена (хеш spec) и тот же движок (хеш сборки) — готовый scene.mp4 (перезапуск после сбоя
    # субтитров/сведения не рендерит заново).
    bundle = ROOT / "node" / "scene3d" / "dist" / "scene3d.js"
    spec_key = hashlib.sha1(json.dumps(built["spec"], ensure_ascii=False, sort_keys=True).encode()
                            + (bundle.read_bytes() if bundle.is_file() else b"")).hexdigest()
    key_file = work / "scene.key"
    if raw_video.is_file() and key_file.is_file() and key_file.read_text("utf-8").strip() == spec_key:
        report["render3d_sec"] = 0.0
        report["render3d_cached"] = True
    else:
        key_file.unlink(missing_ok=True)
        info = S3.render_scene(built["spec"], raw_video, workers=1, gl=args.gl, check_assets=False)
        key_file.write_text(spec_key, "utf-8")
        report["render3d_sec"] = round(time.time() - t3d, 1)
        report["luma_min"] = info.get("luma_min")
    t_cap = time.time()
    capped = work / "caps.mp4"
    burn_captions(plan, words, raw_video, capped, work, total)
    report["captions_sec"] = round(time.time() - t_cap, 1)
    final = out_dir / f"{args.name}.mp4"
    mix(plan, capped, narration, built["sfx"], total, final, work, report, lead)
    S3.contact_sheet(final, out_dir / args.sheet, count=12, cols=4, width=360)
    frames = sentence_frames(final, built, out_dir / "check")
    tile([Path(frames[n]) for n in sorted(frames)], out_dir / "check_sheet.jpg", 6,
         [f"{n}" for n in sorted(frames)])
    (out_dir / "check.md").write_text(P.checklist_md(plan, built, {n: Path(p).name for n, p in frames.items()}), "utf-8")
    shutil.copyfile(narration, work / "narration_used.mp3")
    report.update({"video": str(final), "sheet": str(out_dir / args.sheet), "check": str(out_dir / "check.md"),
                   "total_sec": round(time.time() - started, 1)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
