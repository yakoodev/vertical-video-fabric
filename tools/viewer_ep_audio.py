"""Звук серий соло-ДнД «Мира от зрителей»: подложка из записей паков + радиопомехи + голос огонька → −14 LUFS.

Правило проекта: звуковые эффекты — только настоящие записи (паки MOTION_PACKS_DIR), громкость — в коде
относительно голоса. Записи можно обрабатывать (замедлить, отфильтровать, зациклить):
* гул — freesound/tension.ogg на октаву ниже, по кругу «туда-обратно» (без щелчка на стыке), ФНЧ;
* ветер — freesound/whoosh-4.ogg и whoosh-3.ogg, замедлены и отфильтрованы, порывами;
* огонёк — freesound/triangle.ogg с эхом (появление и меню), тише голоса;
* тень в тумане — freesound/impact-low.ogg на кварту ниже, глухо (только курированные записи из items.json); всплески помех — kenney-interface/glitch_00N.ogg;
* шаги — kenney-impact/footstep_concrete_00N.ogg, когда встаёт.
Радиопомехи — единственное, что генерирует ffmpeg (белый шум в полосе 700–5200 Гц, по уровню помех кадра
fx.keys), — это не «эффект», а эфир (разрешено заказом).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RATE = 48000
TARGET_LUFS = -14.0
# Уровни относительно громкости голоса (дБ LUFS ниже голоса); помехи — на полном уровне (1.0).
UNDER = {"drone": 15.0, "wind": 17.0, "static_full": 6.0, "chime": 9.0, "chime_soft": 14.0, "braam": 6.0,
         "glitch": 9.0, "step": 16.0,
         # серия 1: тики кубика, подтверждение, предмет, писк камеры, шаги Сборщика (ближе — громче), шипение ленты
         "tick": 15.0, "ding": 12.0, "dropitem": 10.0, "beep": 11.0, "stomp": 11.0, "stomp2": 8.0, "stomp3": 5.0}


def _run(args: list[str], what: str) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise SystemExit(f"{what}: {(proc.stderr or proc.stdout).strip()[-1200:]}")


def pack(ref: str) -> Path:
    from app.vtubika_endcard import sound_path

    p = sound_path({"ref": f"sfx:{ref}"})
    if p is None:
        raise SystemExit(f"нет звука пака {ref} (MOTION_PACKS_DIR)")
    return p


def lufs(path: Path) -> float:
    from app.audio_mix_model import ebur128

    return float(ebur128(path)["i"])


def static_level(keys: list[dict], t: float) -> float:
    """Уровень помех 0..1 на время t — та же интерполяция, что fx.ts fxAt (static)."""
    ks = [k for k in keys if "static" in k]
    if not ks:
        return 0.0
    if t <= ks[0]["t"]:
        return float(ks[0]["static"])
    for a, b in zip(ks, ks[1:]):
        if a["t"] <= t < b["t"]:
            return float(a["static"] + (b["static"] - a["static"]) * (t - a["t"]) / max(1e-6, b["t"] - a["t"]))
    return float(ks[-1]["static"])


def stems(total: float, events: dict, fx_keys: list[dict], work: Path) -> dict[str, Path]:
    """Подложки по отдельности (wav 48 кГц моно), каждая — на всю длину ролика."""
    import numpy as np

    out: dict[str, Path] = {}
    T = f"{total:.3f}"
    # Гул: на октаву ниже, палиндром по кругу, ФНЧ, нарастает из помех.
    drone = work / "st_drone.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pack("freesound/tension.ogg")), "-filter_complex",
          f"[0]aresample={RATE},asetrate={RATE // 2},aresample={RATE},lowpass=f=650,asplit[a][b];[b]areverse[r];"
          f"[a][r]concat=n=2:v=0:a=1,aloop=loop=-1:size={RATE * 20},atrim=0:{T},afade=t=in:st=1.6:d=2.4,"
          f"afade=t=out:st={total - 1.2:.3f}:d=1.2[o]", "-map", "[o]", "-ac", "1", "-ar", str(RATE), str(drone)], "гул")
    out["drone"] = drone
    # Ветер: порывы из двух «вжухов», замедленных вдвое с лишним, без верхов.
    gusts = [4.0, 11.5, 19.0, 27.5, 35.5, 43.0]
    srcs = [pack("freesound/whoosh-4.ogg"), pack("freesound/whoosh-3.ogg")]
    parts, inputs = [], []
    for i, t in enumerate(g for g in gusts if g < total - 1):
        inputs += ["-i", str(srcs[i % 2])]
        parts.append(f"[{i}]aresample={RATE},asetrate={int(RATE * 0.42)},aresample={RATE},highpass=f=120,lowpass=f=1400,"
                     f"afade=t=in:d=0.8,adelay={int(t * 1000)}:all=1[g{i}]")
    wind = work / "st_wind.wav"
    graph = ";".join(parts) + ";" + "".join(f"[g{i}]" for i in range(len(parts))) + \
        f"amix=inputs={len(parts)}:normalize=0,apad,atrim=0:{T}[o]"
    _run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-ac", "1", "-ar", str(RATE),
          str(wind)], "ветер")
    out["wind"] = wind
    # Помехи: шум в полосе, огибающая — уровень помех кадра (fx.keys), с «треском» (короткие провалы).
    noise = work / "st_noise.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anoisesrc=color=white:sample_rate={RATE}:amplitude=0.5:seed=7",
          "-af", f"highpass=f=700,lowpass=f=5200,atrim=0:{T}", "-ac", "1", str(noise)], "помехи")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(noise), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    tt = np.arange(len(x)) / RATE
    times = np.array([k["t"] for k in fx_keys if "static" in k])
    vals = np.array([k["static"] for k in fx_keys if "static" in k])
    env = np.interp(tt, times, vals) if len(times) else np.zeros_like(tt)
    crackle = np.repeat((np.random.default_rng(3).random(len(x) // 480 + 1) > 0.12).astype(np.float32), 480)[:len(x)]
    y = x * env * (0.55 + 0.45 * crackle)
    st = work / "st_static.wav"
    _write_wav(st, y)
    out["static"] = st
    out["_static_full_ref"] = noise
    return out


def _write_wav(path: Path, y) -> None:
    import wave

    import numpy as np

    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def hits(events: dict, glitch_times: list[float], total: float) -> list[tuple[str, Path, float, str]]:
    """Короткие звуки на событиях: (уровень из UNDER, файл, время, фильтры)."""
    out = [("chime", pack("freesound/triangle.ogg"), events["appear"] + 0.3, "aecho=0.8:0.6:140|300:0.35|0.18"),
           ("chime_soft", pack("freesound/triangle.ogg"), events["menu"] + 0.2,
            f"aresample={RATE},asetrate={int(RATE * 0.84)},aresample={RATE},aecho=0.8:0.6:160:0.3"),
           ("braam", pack("freesound/impact-low.ogg"), events["g1"] - 0.05,
            f"aresample={RATE},asetrate={int(RATE * 0.72)},aresample={RATE},lowpass=f=900"),
           ("step", pack("kenney-impact/footstep_concrete_001.ogg"), events["stand"] + 0.9, ""),
           ("step", pack("kenney-impact/footstep_concrete_003.ogg"), events["stand"] + 1.35, "")]
    for i, t in enumerate(glitch_times):
        out.append(("glitch", pack(f"kenney-interface/glitch_00{1 + i % 4}.ogg"), t, ""))
    return [h for h in out if 0 <= h[2] < total]


def mix(video: Path, voice: Path, lead: float, total: float, events: dict, fx_keys: list[dict], glitch_times: list[float],
        out: Path, work: Path, hits_fn=None, extras: list[dict] | None = None) -> dict:
    """Голос (с lead) + подложки + звуки событий по уровням UNDER → −14 LUFS, видео копией. ``hits_fn`` — свой набор
    звуков на событиях (как ``hits``); ``extras`` — дорожки-вставки ``{name, path, at, rel_db, af}``: громкость —
    относительно голоса (rel_db дБ LUFS), фильтры ``af`` (серия 1: её запись на старой плёнке, шипение ленты)."""
    from app.stories_3d import voice_lufs

    v_lufs = voice_lufs(voice)
    st = stems(total, events, fx_keys, work)
    report: dict = {"voice_lufs": round(v_lufs, 2), "gains": {}}
    inputs = ["-i", str(video), "-i", str(voice)]
    chains = [f"[1]aresample={RATE},aformat=channel_layouts=mono,adelay={int(lead * 1000)}:all=1,apad,atrim=0:{total:.3f}[voice]"]
    labels = ["[voice]"]
    n = 2

    def add(path: Path, name: str, gain_db: float, extra: str = "", at: float = 0.0) -> None:
        nonlocal n
        inputs.extend(["-i", str(path)])
        delay = f",adelay={int(at * 1000)}:all=1" if at else ""
        chains.append(f"[{n}]aresample={RATE},aformat=channel_layouts=mono{(',' + extra) if extra else ''}"
                      f",volume={gain_db:.2f}dB{delay},apad,atrim=0:{total:.3f}[s{n}]")
        labels.append(f"[s{n}]")
        report["gains"][f"{name}@{at:.2f}" if at else name] = round(gain_db, 2)
        n += 1

    for name in ("drone", "wind"):
        add(st[name], name, v_lufs - UNDER[name] - lufs(st[name]))
    # Помехи: громкость шума на уровне 1.0 → голос − static_full; огибающая уже внутри.
    add(st["static"], "static", v_lufs - UNDER["static_full"] - lufs(st["_static_full_ref"]))
    for ex in extras or []:
        add(Path(ex["path"]), ex["name"], v_lufs + float(ex["rel_db"]) - lufs(Path(ex["path"])), ex.get("af", ""), at=float(ex["at"]))
    for name, path, t, af in (hits_fn or hits)(events, glitch_times, total):
        src_l = lufs(path)
        add(path, name, v_lufs - UNDER[name] - (src_l if src_l > -70 else -20), af, at=t)
    graph = ";".join(chains) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[mix]"
    pre = work / "mixed_pre.mp4"
    _run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[mix]", "-c:v", "copy",
          "-c:a", "pcm_s16le", "-t", f"{total:.3f}", str(pre.with_suffix(".mov"))], "сведение")
    # Громкость −14 LUFS одним усилением + ограничитель: loudnorm в один проход «подтягивает» тихие места (помехи
    # вступления становились громче голоса) — соотношения слоёв должны остаться как сведены.
    gain = TARGET_LUFS - lufs(pre.with_suffix(".mov"))
    report["master_gain_db"] = round(gain, 2)
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(pre.with_suffix(".mov")), "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-af",
          f"volume={gain:.2f}dB,alimiter=limit=0.78:attack=3:release=60:level=false", "-c:a", "aac", "-b:a", "192k",
          "-ar", "48000", "-movflags", "+faststart", str(out)], "громкость")
    report["final_lufs"] = round(lufs(out), 2)
    return report


if __name__ == "__main__":  # pragma: no cover
    sys.exit("модуль tools/viewer_ep.py")
