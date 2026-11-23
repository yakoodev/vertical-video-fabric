"""3D-серия: озвучка по фразам — одна запись TTS на фразу, между фразами пауза автора.

Зачем. Целиком текст нейронка читает одним дыханием, паузы ставит сама, а подгонка под темп (``series3d_voice``)
их ещё и укорачивает. Ч.5–6 «Лифта» собирали руками по фразам: двери едут, шаги слышны — после фразы нужная
тишина, и длина бега/шагов в постановке посчитана под неё. Сценарий говорит это сам:

* ``script.narration = "sentences"`` — озвучка по фразам (по умолчанию — целиком, как раньше);
* ``sentence.pause_after`` — тишина после фразы, с (нет — ``DEFAULT_PAUSE``);
* ``sentence.voice_fx`` — обработка голоса фразы (``VOICE_FX``: голос героя из темноты снизу или шёпот сверху), тем
  же голосом проекта — другой голос не подставляем (память project-voice-locked).

Каждая фраза — тем же провайдером и голосом мира (прямая речь героя — его голосом, как ``synthesize_dialog``),
тишина по краям срезается (как ``narr.py`` агента ч.5–6), дальше — пауза автора. Подгонки под темп нет: длина
пауз — замысел автора. Бесплатно у Gemini (0 ₽), цена у платного TTS — та же, что за весь текст.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

SR = 48000
LEAD_SEC = 0.05            # тишина до первой фразы
DEFAULT_PAUSE = 0.35       # пауза после фразы, если автор не сказал
MAX_PAUSE = 6.0
TRIM_HEAD, TRIM_TAIL = 0.03, 0.08
FADE_SEC = 0.005
MODES = ("whole", "sentences")
# Обработка голоса фразы (ffmpeg), громкость — относительно рассказчика. Ч.6: голос Дани из темноты.
VOICE_FX = {
    # снизу из пролёта: на тон ниже, глуше, с эхом лестницы
    "below": ("asetrate=48000*0.89,aresample=48000,atempo=1.06,lowpass=f=3200,highpass=f=140,"
              "aecho=0.8:0.75:120|260|430:0.38|0.25|0.14,volume=1.15,apad=pad_dur=0.6", 0.5),
    # сверху вплотную: шёпот (фаза спектра случайная — голос без тона), сухо
    "above": ("afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)':imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)'"
              ":win_size=512:overlap=0.75,highpass=f=300,lowpass=f=7500,volume=1.8", 0.1),
}


class LinesError(RuntimeError):
    pass


def mode(script: dict | None, options: dict | None = None) -> str:
    """Как озвучивать: ``script.narration`` (или опция эпизода ``narration``) — ``sentences`` | ``whole``."""
    raw = (script or {}).get("narration") or (options or {}).get("narration") or "whole"
    return raw if raw in MODES else "whole"


def plan(sentences: list[dict], names: tuple[str, ...] = (), dialog: bool = False) -> list[dict]:
    """Фразы сценария → строки озвучки ``[{text, pause, fx?, dialog?}]``. ``dialog`` — у фразы с прямой речью героя
    куски по ролям (``series3d_blender.dialog_lines``)."""
    out = []
    for s in sentences:
        text = " ".join(str(s.get("text") or "").split())
        if not text:
            continue
        pause = s.get("pause_after")
        row: dict[str, Any] = {"text": text, "pause": round(min(MAX_PAUSE, max(0.0, float(DEFAULT_PAUSE if pause is None
                                                                                           else pause))), 3)}
        if s.get("voice_fx") in VOICE_FX:
            row["fx"] = s["voice_fx"]
        if dialog:
            from app.series3d_blender import dialog_lines

            parts = dialog_lines([s], names)
            if len({p["speaker"] for p in parts}) > 1 or any(p["speaker"] == "hero" for p in parts):
                row["dialog"] = parts
        out.append(row)
    return out


def key_text(text: str, lines: list[dict]) -> str:
    """Текст для ключа записи: паузы и обработка входят — другая пауза, другая запись (старые ключи «целиком» прежние)."""
    import json

    return text + "\n#lines " + json.dumps([[ln["pause"], ln.get("fx") or ""] for ln in lines], ensure_ascii=False)


def _decode(path: Path, fx: str | None = None) -> Any:
    import numpy as np

    cmd = ["ffmpeg", "-v", "error", "-i", str(path)] + (["-af", fx] if fx else []) + \
        ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=True, timeout=120)
    return np.frombuffer(proc.stdout, np.float32).copy()


def trim(a: Any, tail: float = TRIM_TAIL) -> Any:
    """Тишину по краям фразы долой (огибающая 10 мс, порог 2 % пика), края — короткое затухание."""
    import numpy as np

    if len(a) == 0:
        return a
    env = np.convolve(np.abs(a), np.ones(480) / 480, mode="same")
    idx = np.nonzero(env > max(1e-3, float(env.max()) * 0.02))[0]
    if len(idx) == 0:
        return a[:0]
    s0, s1 = max(0, int(idx[0]) - int(TRIM_HEAD * SR)), min(len(a), int(idx[-1]) + int(tail * SR))
    seg = np.array(a[s0:s1], dtype=np.float32)
    fade = int(FADE_SEC * SR)
    if len(seg) > 2 * fade:
        seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return seg


def _write_wav(path: Path, pcm: Any) -> None:
    import wave

    import numpy as np

    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())


def synthesize_lines(tts: Any, lines: list[dict], out_path: Path, voices: dict | None = None) -> Any:
    """Каждая строка — своя запись тем же ``tts`` (с ``dialog`` — по ролям), склейка с паузами → ``out_path`` (mp3).
    → ``Speech`` (цена — сумма записей, ``raw_usage.spans`` — где звучит каждая фраза, с)."""
    import numpy as np

    from app.dubbing import Speech

    if not lines:
        raise LinesError("нет фраз для озвучки")
    out_path = Path(out_path)
    work = out_path.with_name(out_path.stem + ".lines")
    work.mkdir(parents=True, exist_ok=True)
    chunks, spans, t, cost = [np.zeros(int(SR * LEAD_SEC), np.float32)], [], LEAD_SEC, 0.0
    paid = False
    try:
        for i, ln in enumerate(lines):
            raw = work / f"{i:02d}.mp3"
            if ln.get("dialog") and hasattr(tts, "synthesize_dialog"):
                speech = tts.synthesize_dialog(ln["dialog"], raw, voices or {})
            else:
                speech = tts.synthesize(ln["text"], raw)
            if speech.cost_rub is not None:
                cost += float(speech.cost_rub)
            else:
                paid = True
            seg = trim(_decode(Path(speech.path)))
            fx = VOICE_FX.get(str(ln.get("fx") or ""))
            if fx is not None and len(seg):
                # обработка — после обрезки: эхо пролёта звучит дальше фразы
                tmp = work / f"{i:02d}.trim.wav"
                _write_wav(tmp, seg)
                seg = trim(_decode(tmp, fx[0]), tail=fx[1])
            spans.append({"i": i, "t0": round(t, 3), "t1": round(t + len(seg) / SR, 3)})
            chunks += [seg, np.zeros(int(SR * float(ln.get("pause") or 0.0)), np.float32)]
            t += len(seg) / SR + float(ln.get("pause") or 0.0)
        wav = work / "all.wav"
        _write_wav(wav, np.concatenate(chunks))
        codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if out_path.suffix == ".mp3" else []
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), *codec, str(out_path)], check=True,
                       capture_output=True, timeout=120)
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    return Speech(out_path, None if paid else round(cost, 4), {"spans": spans, "lines": len(lines)})
