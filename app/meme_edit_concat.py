"""Мем-эдит: склейка отрендеренных блоков в один ролик с одной музыкальной подложкой (как пилот 2, concat2.py).

Клип сервиса держит ≤ 40 кусков и ≤ 40 вставок, поэтому длинный эдит — по блоку (бою) на клип, рендер по одному,
потом склейка здесь:

* звук каждого блока подогнан ровно под длину его картинки (кадры / fps) — за 5 минут звук не уезжает;
* музыка — одна подложка: отрезки трека с сильных долей (app/meme_edit_plan.music_segments), швы — на стыках блоков
  (там переход), 30 мс наплыв против щелчка; под голосом −12 дБ, под мемом −16 дБ, тишина перед последней репликой
  и возврат на последнем меме, затухание 0.5 с в конце;
* белая вспышка (4 кадра) на стыках актов; громкость −14 LUFS, пик −1.5 dBTP (loudnorm в два прохода);
* картинка — concat демуксером и одно перекодирование.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

SR = 48000
DUCK_VOICE_DB = -12.0
DUCK_MEME_DB = -16.0
MUSIC_DB = -7.0
MUSIC_REF_DBFS = -20.0


def _run(cmd: list[str], timeout: float = 3600) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"{' '.join(cmd[:6])}…: {proc.stderr[-800:]}")
    return proc


def video_frames(path: Path) -> tuple[int, float]:
    """Число кадров и частота картинки файла — из самого файла (ffprobe), не по умолчанию."""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                "stream=nb_read_packets,r_frame_rate,avg_frame_rate", "-of", "json", str(path)]).stdout
    s = json.loads(out)["streams"][0]
    for key in ("r_frame_rate", "avg_frame_rate"):
        num, _sep, den = str(s.get(key) or "0/0").partition("/")
        try:
            rate = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return int(s["nb_read_packets"]), rate
    raise RuntimeError(f"ffprobe не знает частоту кадров {Path(path).name}")


def decode(path: Path | str, ch: int = 2, start: float | None = None, dur: float | None = None) -> Any:
    import numpy as np

    cmd = ["ffmpeg", "-nostdin", "-v", "error"] + (["-ss", f"{start:.4f}"] if start is not None else []) + \
          (["-t", f"{dur:.4f}"] if dur is not None else []) + ["-i", str(path), "-vn", "-ac", str(ch), "-ar", str(SR),
                                                               "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    raw = proc.stdout or b""
    return np.frombuffer(raw[: len(raw) // (4 * ch) * 4 * ch], np.float32).reshape(-1, ch).copy()


def duck_db(total: float, words: list[tuple[float, float]], memes: list[tuple[float, float]],
            silence: tuple[float, float] | None = None, step: float = 0.01, voice_db: float = DUCK_VOICE_DB,
            meme_db: float = DUCK_MEME_DB) -> Any:
    """Громкость музыки по времени (дБ к базовой, шаг ``step``): под словами (с запасом −0.15/+0.3 с, паузы < 0.4 с
    слиты) ``voice_db``, под мемами ``meme_db``, на ``silence`` — тишина; сглажено 60 мс (чистая функция)."""
    import numpy as np

    grid = np.arange(0, total + step / 2, step)
    db = np.zeros_like(grid)
    spans: list[list[float]] = []
    for a, b in sorted(words):
        a, b = a - 0.15, b + 0.30
        if spans and a <= spans[-1][1] + 0.4:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    for a, b in spans:
        sel = (grid >= a) & (grid <= b)
        db[sel] = np.minimum(db[sel], voice_db)
    for a, b in memes:
        sel = (grid >= a) & (grid <= b)
        db[sel] = np.minimum(db[sel], meme_db)
    if silence:
        db[(grid >= silence[0]) & (grid < silence[1])] = -80.0
    k = max(1, int(round(0.03 / step)))
    db = np.convolve(np.pad(db, (k, k), mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
    return grid, db


def concat_reel(blocks: list[dict], files: list[Path], out: Path, *, music_path: str | None, segments: list[dict],
                work: Path, flashes: bool = True, music_db: float = MUSIC_DB,
                progress: Callable[[str], None] | None = None) -> dict:
    """Склейка блоков ``files`` (по порядку ``blocks`` из плана) в ``out``. Ответ — шкала готового ролика."""
    import numpy as np

    work.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda _s: None)
    segs, t, fps = [], 0.0, None
    for b, path in zip(blocks, files):
        n, rate = video_frames(path)
        fps = fps or rate
        # длина блока — по плану (кадры на долях): рендер мог потерять кадр-другой в хвосте — стоп-кадр добивает,
        # иначе к концу ролика стыки уезжают с долей музыки. Без плана — секунды по СВОЕЙ частоте файла (n / rate):
        # блоки 60 и 120 к/с в одном ролике (журнал 15.11: запись 60 к/с, прочитанная как 120, — двойная скорость).
        want = int(round(float(b.get("total") or n / (rate or fps)) * fps)) or n
        segs.append({"block": b, "path": path, "frames": want, "rendered": n, "at": round(t, 4), "len": want / fps})
        t += want / fps
    total = t
    say("звук блоков")
    parts = []
    for s in segs:
        a = decode(s["path"])
        need = int(round(s["len"] * SR))
        parts.append(a[:need] if len(a) >= need else np.vstack([a, np.zeros((need - len(a), 2), np.float32)]))
    voice = np.vstack(parts) if parts else np.zeros((0, 2), np.float32)
    del parts
    n = len(voice)
    words, memes, joins = [], [], []
    for s in segs:
        for w in s["block"]["words"]:
            words.append((s["at"] + w["start"], s["at"] + w["end"]))
        for ins in s["block"]["inserts"]:
            memes.append((s["at"] + ins["at"], s["at"] + ins["at"] + ins["slot"]))
        joins.append(s["at"])
    flash_at = [s["at"] for s in segs[1:] if s["block"].get("flash_in")] if flashes else []
    silence = None
    last = segs[-1]["block"] if segs else None
    if last and last["inserts"] and last["inserts"][-1].get("line_start") is not None:
        ins = last["inserts"][-1]
        silence = (segs[-1]["at"] + float(ins["line_start"]) - 0.2, segs[-1]["at"] + float(ins["at"]))
    mix = voice
    seams: list[float] = []
    if music_path and Path(music_path).is_file() and segments:
        say("музыка")
        track = decode(music_path)
        body = track[int(SR * float(segments[0]["pos"])):]
        rms = float(np.sqrt(np.mean(body ** 2))) if len(body) else 0.0
        # швы — на настоящих стыках блоков (кадры рендера), длины отрезков — по ним
        starts = [0.0] + [min(joins, key=lambda j: abs(j - float(seg["t"]))) for seg in segments[1:]]
        segments = [{**seg, "t": st, "len": (starts[i + 1] if i + 1 < len(starts) else total) - st}
                    for i, (seg, st) in enumerate(zip(segments, starts))]
        pieces = []
        for seg in segments:
            a = int(round(float(seg["pos"]) * SR))
            length = int(round(float(seg["len"]) * SR))
            x = track[a:a + length]
            if len(x) < length:
                x = np.vstack([x, np.zeros((length - len(x), 2), np.float32)])
            pieces.append(x)
        mus = np.vstack(pieces)[:n]
        if len(mus) < n:
            mus = np.vstack([mus, np.zeros((n - len(mus), 2), np.float32)])
        seams = [float(seg["t"]) for seg in segments[1:]]
        for sm in seams:
            s0, w = int(sm * SR), int(0.03 * SR)
            if w < s0 < n - w:
                mus[s0 - w:s0] *= np.linspace(1, 0, w, dtype=np.float32)[:, None]
                mus[s0:s0 + w] *= np.linspace(0, 1, w, dtype=np.float32)[:, None]
        grid, db = duck_db(total, words, memes, silence)
        db = db + music_db
        fade = np.clip((total - grid) / 0.5, 0, 1)
        gain = np.interp(np.arange(n) / SR, grid, (10 ** (db / 20)) * fade).astype(np.float32)
        ref = 10 ** ((MUSIC_REF_DBFS - 20 * np.log10(rms + 1e-9)) / 20)
        mix = voice + mus * (ref * gain)[:, None]
        del mus, track
    raw = work / "mix.wav"
    say("сведение")
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                           "-i", "pipe:0", str(raw)], input=mix.astype(np.float32).tobytes(), capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-800:])
    del mix, voice
    r = _run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(raw), "-af",
              "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    js = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    rate = fps or 30.0
    boxes = []
    for fl in flash_at:
        for k2, alpha in ((-1, 0.55), (0, 0.95), (1, 0.6), (2, 0.25)):
            a = fl + k2 / rate
            boxes.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@{alpha}:t=fill:"
                         f"enable='between(t,{a - 0.001:.4f},{a + 1 / rate - 0.002:.4f})'")
    graph = []
    for i, sg in enumerate(segs):
        pad = max(0, sg["frames"] - sg["rendered"]) + 1
        graph.append(f"[{i}:v]fps={rate:g},setpts=N/{rate:g}/TB,tpad=stop_mode=clone:stop={pad},"
                     f"trim=end_frame={sg['frames']},setpts=N/{rate:g}/TB,format=yuv420p[v{i}]")
    graph.append("".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[vc]")
    graph.append("[vc]" + (",".join(boxes) if boxes else "null") + "[v]")
    inputs: list[str] = []
    for sg in segs:
        inputs += ["-i", str(sg["path"])]
    say("картинка")
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-i", str(raw), "-filter_complex", ";".join(graph),
          "-map", "[v]", "-map", f"{len(segs)}:a:0", "-af", ln + ",aresample=48000",
          "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", f"{rate:g}",
          "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out)])
    raw.unlink(missing_ok=True)
    return {"total": round(total, 3), "fps": rate, "blocks": [{"key": s["block"]["key"], "at": s["at"],
                                                              "len": round(s["len"], 3)} for s in segs],
            "flashes": [round(f, 3) for f in flash_at], "seams": seams, "memes": [list(map(lambda v: round(v, 3), m))
                                                                                  for m in memes],
            "silence": silence, "loudnorm_in": {k: js.get(k) for k in ("input_i", "input_tp")}}
