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
7. **Концовка** — карточка бренда (``app.vtubika_endcard``: значок, «vtubika», сайт, слоган сайта) ~2.8 с после
   ролика, мягкий стинг паков, музыка затихает под ней; ``"end_card": false`` в плане — без неё.
8. **Проверка** — лист кадров (12), лист концовки (endcard_sheet.jpg), кадр на каждую фразу и список «фраза → что в
   кадре» (check.md).

Озвучка читает термины по словарю произношения (``app.vtubika_say``, ``app.vtubika.PRONOUNCE`` + ``pronounce``
плана): «Live2D» → «лайв-два-дэ» только в тексте TTS; субтитры — как в сценарии, слова Whisper сливаются обратно в
термин (``heard_as_script``).

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
sys.path.insert(0, str(ROOT / "tools"))

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
    from app import vtubika_say as SAY

    # Термины словаря произношения: услышанное «лайв два дэ» → «Live2D» сценария (на том же времени).
    heard_raw = transcribe_words(raw)
    heard = SAY.heard_as_script(heard_raw, SAY.pronounce_map(plan))
    text = P.caption_text(plan)
    share = heard_share(text, heard)
    if share < 0.6:
        raise RuntimeError(f"в озвучке распознано {share:.0%} текста — не та речь")
    words = script_words(text, heard)
    sentences = [{"text": s["text"]} for s in plan["sentences"]]
    fit = VF.fit_narration(raw, fitted, sentences, words, tempo_cap=cap)
    info = {k: fit[k] for k in ("tempo", "audio_sec", "source_sec", "wps_before", "wps_after", "speech_end")}
    info["heard_share"] = share
    cache.write_text(json.dumps({"words": fit["words"], "fit": info, "heard": heard_raw}, ensure_ascii=False), "utf-8")
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


FLOW_ASSET = "1"


def flow_layers(shots: list[dict], total: float, fps: int = 30) -> list[dict]:
    """Перетекающие переходы (слой ``transition`` моушна, frontend/src/motion/flow.ts) на склейках планов с полем
    ``flow`` в плане: ``{"cam": "monitor", "at": "над", "flow": {"kind": "zoom_through", "dur": 0.8, "w": 300, …}}``.

    Сцены A и B — видео-слои того же отрисованного ролика: A — до склейки живьём и стоп-кадр после, B — стоп-кадр
    первого кадра до склейки и живьём после (у ролика нет «B до склейки»). Переход объединяет их в группы и ведёт
    маску/матрицу; концы отрезка совпадают с роликом без перехода."""
    from app.motion_flow import FLOW_DEFAULT_SEC

    def vid(lid: str, a: float, b: float, src: float, hold: bool = False) -> dict:
        props = {"asset_id": FLOW_ASSET, "from": round(src, 4), "w": 1080}
        if hold:
            props["hold"] = True
        return {"id": lid, "type": "video", "start": round(a, 4), "end": round(b, 4), "track": 1, "x": 0.5, "y": 0.5,
                "props": props, "in": None, "out": None}

    out: list[dict] = []
    for i, sh in enumerate(shots):
        fl = sh.get("flow")
        if not fl or i == 0:
            continue
        kind = str(fl.get("kind") or "push_slide")
        h = float(fl.get("dur") or FLOW_DEFAULT_SEC.get(kind, 0.6)) / 2
        c = round(float(sh["t0"]) * fps) / fps
        if c - h < 0 or c + h > total:
            continue
        a, ah, bh, b = f"fa{i}", f"fah{i}", f"fbh{i}", f"fb{i}"
        out += [vid(a, c - h, c, c - h), vid(ah, c, c + h, c - 1 / fps, hold=True),
                vid(bh, c - h, c, c, hold=True), vid(b, c, c + h, c)]
        props = {k: v for k, v in fl.items() if k not in ("dur", "x", "y")}
        props.update({"kind": kind, "from": [a, ah], "to": [bh, b]})
        out.append({"id": f"tr{i}", "type": "transition", "start": round(c - h, 4), "end": round(c + h, 4), "track": 0,
                    "x": float(fl.get("x", 0.5)), "y": float(fl.get("y", 0.5)), "props": props, "in": None, "out": None})
    return out


def burn_captions(plan: dict, words: list[dict], video: Path, out: Path, work: Path, total: float,
                  y: float | None = None, shots: list[dict] | None = None) -> None:
    from app import vtubika_plan as P
    from app.motion_render_comp import build_compositor_args, write_comp
    from app.motion_render_video import prepare_video_frames
    from app.stories_3d import _captions
    from app.stories_library import DEFAULT_CAPTIONS

    props = _captions({"captions": dict(DEFAULT_CAPTIONS)})
    comp_words, marks = P.caption_words(words, plan.get("highlight") or {})
    props["highlightWords"] = marks
    layer = {"id": "caps", "type": "captions", "start": 0.0, "end": round(total, 3), "x": 0.5,
             "y": float(DEFAULT_CAPTIONS["y"] if y is None else y), "props": props, "track": 9}
    flows = flow_layers(shots or [], total)
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=30, duration=total, layers=[*flows, layer], camera=[],
                      words=comp_words)
    (work / "assets.json").write_text(json.dumps({FLOW_ASSET: str(video)} if flows else {}), "utf-8")
    videos = None
    if flows:
        videos, notes = prepare_video_frames(flows, {FLOW_ASSET: str(video)}, work, fps=30, out_width=1080)
        if notes or videos is None:
            raise RuntimeError(f"переходы: кадры видео-слоёв не нарезались: {notes}")
    # stdin=DEVNULL: компоновщик (node + @napi-rs/canvas) на Windows падает 0xC0000409 с недействительным
    # унаследованным stdin (запуск из агента/планировщика).
    proc = subprocess.run(build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out,
                                                videos_path=videos),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-600:]}")


def mix(plan: dict, video: Path, narration: Path, sfx: list[dict], total: float, out: Path, work: Path,
        report: dict, lead: float = 0.0, music_fade: float | None = None) -> None:
    """Голос, музыка на under_db тише голоса, звуки паков → громкость −14 LUFS. ``music_fade`` — затухание музыки
    в конце (под концовкой), иначе ``fade_out`` плана."""
    from app import vtubika as U
    from app.audio_mix_model import ebur128
    from app.motion import asset_key
    from app.motion_render import build_audio_layers_args, speech_level_db
    from app.stories_3d import voice_lufs
    from app.vtubika_endcard import sound_path

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
                                 "fadeIn": music.get("fade_in", 1.0),
                                 "fadeOut": music_fade if music_fade is not None else music.get("fade_out", 2.5)}})
        report["music"] = {"file": music_path.name, "lufs": m_lufs, "voice_lufs": voice_db, "gain_db": gain,
                           "under_db": music["under_db"]}
    else:
        notes.append(f"нет музыки {music_path}")
    for n, s in enumerate(sfx):
        path = sound_path(s)  # звук пака (ref) или свой файл бренда в репозитории (path)
        key = s.get("ref") or s.get("path")
        if path is None:
            notes.append(f"нет звука {key}")
            continue
        assets[asset_key(key)] = str(path)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": s["t"], "end": round(min(total, s["t"] + 2.5), 3),
                       "props": {"asset_id": key, "volume": s["volume"], "mode": "effect"}})
    pre = work / "mixed.mp4"
    args = build_audio_layers_args(video, pre, layers, assets, total, clip_has_audio=False,
                                   speech_db=speech_level_db(narration), notes=notes)
    _run(args, "сведение")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-c:v", "copy", "-af",
          f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          "-movflags", "+faststart", str(out)], "громкость")
    report["mix_notes"] = notes
    report["sfx"] = len([x for x in layers if x["id"].startswith("sfx")])


def end_card(plan: dict, video: Path, work: Path, total: float, report: dict) -> tuple[Path, float, list[dict], float | None]:
    """Концовка бренда (``app.vtubika_endcard``) после ролика с субтитрами: (ролик, новая длина, звуки карточки,
    затухание музыки). ``"end_card": false`` в плане — ролик как есть."""
    from app import vtubika_endcard as E

    cfg = E.settings(plan)
    if not cfg:
        return video, total, [], None
    card = E.render(cfg, work)
    out = video.with_name(f"{video.stem}_card.mp4")
    xf = float(cfg.get("crossfade") or 0.0)
    E.append(video, card, out, total, xf)
    dur = float(cfg["duration"])
    start = round(total - xf, 3)  # с наплывом карточка начинается чуть раньше конца истории
    report["end_card"] = {"start": start, "duration": dur, "crossfade": xf, "sting": cfg.get("sting"), "webm": str(card)}
    return out, round(start + dur, 3), E.card_sfx(cfg, start), float(cfg.get("music_fade", dur))


def end_card_sheet(video: Path, report: dict, out: Path) -> None:
    """Лист кадров концовки (6 кадров) — проверка карточки бренда."""
    info = report.get("end_card")
    if not info:
        return
    frames = []
    for k, dt in enumerate((0.15, 0.45, 0.8, 1.15, 1.6, info["duration"] - 0.1)):
        f = out.with_name(f"{out.stem}_{k}.jpg")
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{info['start'] + dt:.3f}", "-i", str(video), "-frames:v", "1",
              "-q:v", "3", str(f)], "кадр концовки")
        frames.append(f)
    tile(frames, out, 6, [f"+{dt:.2f}s" for dt in (0.15, 0.45, 0.8, 1.15, 1.6, info["duration"] - 0.1)])
    for f in frames:
        f.unlink(missing_ok=True)
    report["end_card_sheet"] = str(out)


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


def endcard_compare(args, plan: dict, built: dict, capped: Path, narration: Path, full: float, lead: float,
                    music_fade: float | None, work: Path, report: dict) -> None:
    """Сравнение стингов концовки: то же сведение, другой звук карточки; крупная метка на каждой (``--stings`` —
    какие, по умолчанию все, кроме прежнего kenney_classic; ``--sting-labels`` — метки, иначе номера)."""
    from app import vtubika as U
    from app import vtubika_endcard as E
    from vtubika_endcard_sounds import compare

    cfg, ec = E.settings(plan), report["end_card"]
    names = ([n.strip() for n in args.stings.split(",") if n.strip()] if args.stings
             else [n for n in U.END_CARD_STINGS if n != "kenney_classic"])
    unknown = [n for n in names if n not in U.END_CARD_STINGS]
    if unknown:
        raise SystemExit(f"нет стингов {unknown}: {', '.join(U.END_CARD_STINGS)}")
    labels = [x.strip() for x in args.sting_labels.split(",")] if args.sting_labels else None
    marks = {k: ec["start"] + E.cue(k) for k in ("logo", "dot")}
    report["endcard_sounds"] = {"video": str(Path(args.endcard_sounds)), "levels": compare(
        names, lambda name, path: mix(plan, capped, narration, built["sfx"] + E.card_sfx({**cfg, "sting": name}, ec["start"]),
                                      full, path, work, {}, lead, music_fade),
        ec["start"], ec["duration"], work, Path(args.endcard_sounds), labels=labels, marks=marks)}


def endcard_sounds_only(args, plan: dict, built: dict, total: float, narration: Path, lead: float, work: Path,
                        report: dict) -> int:
    """``--endcard-sounds-only``: сравнение стингов на готовом ролике из кэша (``work/caps_vrm.mp4`` прошлой сборки) —
    без рендера VRM, проверок и листов; report.json не перезаписывается."""
    capped = work / "caps_vrm.mp4"
    if not capped.is_file():
        raise SystemExit(f"нет {capped}: сначала полная сборка VRM")
    capped, full, _sfx, music_fade = end_card(plan, capped, work, total, report)
    if not report.get("end_card"):
        raise SystemExit("в плане нет концовки (end_card: false)")
    endcard_compare(args, plan, built, capped, narration, full, lead, music_fade, work, report)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def run_vrm(args, plan: dict, built: dict, words: list[dict], total: float, narration: Path, lead: float,
            out_dir: Path, work: Path, report: dict, started: float) -> int:
    """Та же история на VRM-героине: спека node/vrm, рендер одним браузером, проверки касаний/кресла/волос,
    субтитры, сведение, лист, «фраза → что в кадре»."""
    from app import scene3d as S3
    from app import vtubika_plan as P
    from app import vtubika_vrm as VRM
    from app.vtubika_vrm import to_vrm_spec

    if args.endcard_sounds_only:
        return endcard_sounds_only(args, plan, built, total, narration, lead, work, report)
    from app import vtubika_room3d as ROOM

    spec = to_vrm_spec(plan, built, words, total)
    # Комната 3D-пресета (presets3d.room): отрисована отдельно той же камерой — подложкой под героиню.
    plates = ROOM.apply(plan, spec, work, gl=args.gl, log=_log)
    if plates:
        report["room3d"] = {"room": ROOM.room_name(plan), "plates": str(plates),
                            "render": json.loads((plates / "render.json").read_text("utf-8")) if (plates / "render.json").is_file() else None}
    spec_path = work / "scene_vrm.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_path), "--bundle",
              str(ROOT / "node" / "vrm" / "dist" / "vrm.js"), "--packs", args.vrm_packs, "--gl", args.gl, "--workers", "1",
              *(["--site", spec["siteDir"]] if spec.get("siteDir") else []), *(["--plates", str(plates)] if plates else [])]
    subprocess.run(["npm", "run", "build:vrm"], cwd=ROOT, check=True, shell=sys.platform == "win32",
                   stdin=subprocess.DEVNULL)
    if args.preview:
        times = ([round((s["t0"] + s["t1"]) / 2, 2) for s in built["sentences"]] if args.preview == "mids"
                 else [float(x) for x in args.preview.split(",")])
        prev_dir = work / "preview_vrm"
        shutil.rmtree(prev_dir, ignore_errors=True)
        r = subprocess.run([*render, "--preview", ",".join(f"{t:.2f}" for t in times), "--preview-dir", str(prev_dir)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"превью VRM: {r.stderr.strip()[-800:]}")
        report["preview"] = str(prev_dir)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    raw_video = work / "scene_vrm.mp4"
    bundle = ROOT / "node" / "vrm" / "dist" / "vrm.js"
    key = hashlib.sha1(spec_path.read_bytes() + bundle.read_bytes()
                       + ((plates / "key.txt").read_bytes() if plates else b"")).hexdigest()
    key_file = work / "scene_vrm.key"
    render_report = work / "scene_vrm.report.json"
    t3d = time.time()
    if not (raw_video.is_file() and key_file.is_file() and key_file.read_text("utf-8").strip() == key):
        # Ключ — только у проверенного ролика: старый ключ при упавшем рендере принял бы испорченный файл за кэш.
        key_file.unlink(missing_ok=True)
        r = subprocess.run([*render, "--out", str(raw_video)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError(f"рендер VRM (код {r.returncode}): {r.stderr.strip()[-1200:]}")
        report["render_vrm"] = json.loads(r.stdout.strip().splitlines()[-1])
        VRM.require_render(report["render_vrm"], spec)
        # Отчёт проверенного рендера (кадры, присутствие героини) — рядом с кэшем: повторный запуск после сбоя на
        # последующих шагах (сведение, лист) не теряет цифры, а report.json не остаётся без них.
        render_report.write_text(json.dumps(report["render_vrm"], ensure_ascii=False), "utf-8")
        key_file.write_text(key, "utf-8")
    elif render_report.is_file():
        report["render_vrm"] = json.loads(render_report.read_text("utf-8"))
    report["render3d_sec"] = round(time.time() - t3d, 1)
    chk = subprocess.run(["node", str(ROOT / "node" / "vrm" / "contact.mjs"), "--spec", str(spec_path), "--packs", args.vrm_packs,
                          "--gl", args.gl], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         stdin=subprocess.DEVNULL)
    report["checks"] = json.loads(chk.stdout.strip().splitlines()[-1]) if chk.stdout.strip() else {"error": chk.stderr[-400:]}
    VRM.require_checks(report["checks"], spec)
    capped = work / "caps_vrm.mp4"
    # Субтитры — текст сценария («девятнадцать», а не «19» распознавания) на временах слов озвучки.
    script = [w for sent in P.token_times(plan, words) for w in sent]
    burn_captions(plan, script, raw_video, capped, work, total, y=0.8, shots=built["shots"])
    report["flow"] = [{"t": round(float(sh["t0"]), 3), **sh["flow"]} for sh in built["shots"] if sh.get("flow")]
    capped, full, card_sfx, music_fade = end_card(plan, capped, work, total, report)
    final = out_dir / f"{args.name}.mp4"
    mix(plan, capped, narration, built["sfx"] + card_sfx, full, final, work, report, lead, music_fade)
    if args.endcard_sounds and report.get("end_card"):
        endcard_compare(args, plan, built, capped, narration, full, lead, music_fade, work, report)
    S3.contact_sheet(final, out_dir / args.sheet, count=12, cols=4, width=360)
    end_card_sheet(final, report, out_dir / "endcard_sheet.jpg")
    frames = sentence_frames(final, built, out_dir / "check")
    tile([Path(frames[n]) for n in sorted(frames)], out_dir / "check_sheet.jpg", 6, [f"{n}" for n in sorted(frames)])
    (out_dir / "check.md").write_text(P.checklist_md(plan, built, {n: Path(p).name for n, p in frames.items()}), "utf-8")
    report.update({"video": str(final), "sheet": str(out_dir / args.sheet), "check": str(out_dir / "check.md"),
                   "engine": "vrm", "total_sec": round(time.time() - started, 1)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["checks"].get("ok") else 3


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
    ap.add_argument("--engine", default="voxel", choices=["voxel", "vrm"],
                    help="vrm — героиня VRM «под 2D-аниме» (node/vrm, app/vtubika_vrm.py)")
    ap.add_argument("--vrm-packs", default=r"D:\vvf-offload\vtubika\vrm\vendor", help="модель VRM и клипы мокапа")
    ap.add_argument("--endcard-sounds", help="VRM: ещё и сравнение стингов концовки (mp4 с номерами вариантов)")
    ap.add_argument("--endcard-sounds-only", action="store_true",
                    help="только сравнение стингов на ролике из кэша work (без рендера VRM)")
    ap.add_argument("--stings", help="стинги для сравнения через запятую (END_CARD_STINGS), по умолчанию все новые")
    ap.add_argument("--sting-labels", help="крупные метки вариантов через запятую (A,B,C), иначе номера")
    args = ap.parse_args(argv)
    if args.endcard_sounds_only and not (args.endcard_sounds and args.engine == "vrm"):
        ap.error("--endcard-sounds-only — вместе с --endcard-sounds OUT.mp4 и --engine vrm")
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
    if args.engine == "vrm":
        return run_vrm(args, plan, built, words, total, narration, lead, out_dir, work, report, started)
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
    capped, full, card_sfx, music_fade = end_card(plan, capped, work, total, report)
    final = out_dir / f"{args.name}.mp4"
    mix(plan, capped, narration, built["sfx"] + card_sfx, full, final, work, report, lead, music_fade)
    S3.contact_sheet(final, out_dir / args.sheet, count=12, cols=4, width=360)
    end_card_sheet(final, report, out_dir / "endcard_sheet.jpg")
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
