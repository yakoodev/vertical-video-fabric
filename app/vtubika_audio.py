"""Стыки озвучки «Витубики» без щелчков и обрывков слов, и проверка готового звука.

Откуда «лаги» (серия 7, 12.11): паузы-«биты» после фраз (``tools/vtubika_story.pace_beats``) вставлялись тишиной
посередине между словами по времени Whisper — без поиска настоящей тишины и без наплыва. Где Whisper ошибался на
десятые («чиби-версия. Три» — конец слова = начало следующего) или пауза была короче его ошибки, разрез попадал в
слово: кусок слова, 0.3 с нулей, остаток — заикание и щелчок на краю нулей. Здесь:

* ``quiet_point`` — где резать: середина самой длинной тишины в окне вокруг границы слов (по энергии), а если тишины
  нет — самое тихое место;
* ``insert_pauses`` — вставка тишины в эти точки с короткими наплывами по краям (без ступеньки до нуля);
* ``join_levels`` — уровень по обе стороны каждой точки разреза: у чистого стыка — тишина с обеих сторон;
* ``word_cuts`` — проверка любой дорожки голоса: речь, оборванная «цифровой тишиной» (вставленными нулями), —
  разрез по слову на любом шаге (пауза, склейка, подмена фразы);
* ``voice_in_mix`` — готовый ролик против голоса: провалы голоса в сведении и уезжающая синхронность.

Всё — на моно float32 ``SR`` Гц (``app.series3d_voice.decode``).
"""

from __future__ import annotations

from typing import Any

SR = 48000
FRAME = 0.005
SILENT_DB = -34.0      # тише громкой речи (95-й процентиль) на столько — пауза (как series3d_voice.find_silences)
PAUSE_MIN_SEC = 0.04   # тишина короче — смычка согласного внутри слова, не пауза
FADE_SEC = 0.012       # наплыв на краях вставленной тишины
DIGITAL_DB = -75.0     # «цифровая тишина»: вставленные нули (после mp3 — около −90 dBFS)
DIGITAL_MIN_SEC = 0.06
EDGE_SEC = 0.02        # окно у края цифровой тишины
EDGE_SPEECH_DB = -24.0  # речь у края: громче этого относительно громкой речи — разрез по слову
DROP_DB = 12.0         # провал голоса в сведении относительно обычного уровня
DROP_MIN_SEC = 0.06
SYNC_MAX_MS = 25.0


def _np():
    import numpy as np

    return np


def frame_db(pcm: Any, frame: float = FRAME, sr: int = SR) -> Any:
    """Уровень по окнам ``frame`` секунд, dBFS."""
    np = _np()
    n = max(1, int(sr * frame))
    m = len(pcm) // n
    if not m:
        return np.zeros(0, dtype=np.float32)
    rms = np.sqrt((np.asarray(pcm[: m * n], dtype=np.float64).reshape(m, n) ** 2).mean(axis=1))
    return 20.0 * np.log10(rms + 1e-9)


def speech_level(db: Any) -> float:
    """Громкая речь: 95-й процентиль уровня окон (как у поиска пауз озвучки)."""
    np = _np()
    return float(np.percentile(db, 95)) if len(db) else -100.0


def quiet_point(pcm: Any, lo: float, hi: float, *, sr: int = SR, level: float | None = None) -> float:
    """Где резать между ``lo`` и ``hi`` (с): середина самой длинной тишины (не короче ``PAUSE_MIN_SEC``), иначе
    самое тихое окно."""
    np = _np()
    lo, hi = max(0.0, lo), min(len(pcm) / sr, hi)
    if hi - lo < 2 * FRAME:
        return round((lo + hi) / 2, 4)
    db = frame_db(pcm[int(lo * sr): int(hi * sr)], FRAME, sr)
    if not len(db):
        return round((lo + hi) / 2, 4)
    ref = speech_level(frame_db(pcm, FRAME, sr)) if level is None else level
    quiet = db < ref + SILENT_DB
    best, start = None, None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if best is not None and (best[1] - best[0]) * FRAME >= PAUSE_MIN_SEC:
        return round(lo + (best[0] + best[1]) / 2 * FRAME, 4)
    k = int(np.argmin(db))
    return round(lo + (k + 0.5) * FRAME, 4)


def insert_pauses(pcm: Any, cuts: list[tuple[float, float]], *, sr: int = SR, fade: float = FADE_SEC) -> Any:
    """Тишина ``gap`` секунд в точки ``t`` (по возрастанию): звук до точки затухает, после — нарастает за ``fade``."""
    np = _np()
    n = max(1, int(fade * sr))
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    parts, prev = [], 0
    src = np.asarray(pcm, dtype=np.float32)
    for t, gap in cuts:
        k = int(round(t * sr))
        seg = src[prev:k].copy()
        if prev and len(seg) > n:
            seg[:n] *= ramp
        if len(seg) > n:
            seg[-n:] *= ramp[::-1]
        parts += [seg, np.zeros(int(round(gap * sr)), dtype=np.float32)]
        prev = k
    tail = src[prev:].copy()
    if prev and len(tail) > n:
        tail[:n] *= ramp
    parts.append(tail)
    return np.concatenate(parts)


def word_cuts(pcm: Any, *, sr: int = SR) -> list[dict]:
    """Слово, оборванное «цифровой тишиной» (вставленные нули не короче ``DIGITAL_MIN_SEC``): перед нулями сразу
    речь. Свои паузы TTS так не кончаются (голос затухает до нулей), а разрез по слову — да; начало речи после нулей
    не считаем: у TTS фразы и так начинаются с тишины без наплыва (замер серии 7: первые 1.5 мс — 0.1% пика).
    → ``[{t, db}]`` (t — начало нулей, с; db — уровень речи перед ними относительно громкой речи)."""
    db = frame_db(pcm, FRAME, sr)
    if not len(db):
        return []
    ref = speech_level(db)
    digital = db < DIGITAL_DB
    w = max(1, int(EDGE_SEC / FRAME))
    out: list[dict] = []
    start = None
    for i, z in enumerate(list(digital) + [False]):
        if z and start is None:
            start = i
        elif not z and start is not None:
            if (i - start) * FRAME >= DIGITAL_MIN_SEC and start >= w:
                lvl = float(db[start - w:start].max()) - ref
                if lvl > EDGE_SPEECH_DB:
                    out.append({"t": round(start * FRAME, 3), "db": round(lvl, 1)})
            start = None
    return out


def join_levels(pcm: Any, cuts: list[float], *, sr: int = SR, win: float = 0.03) -> list[dict]:
    """Уровень звука по обе стороны точек разреза (до вставки паузы) относительно громкой речи: у чистого стыка обе
    стороны — тишина (ниже ``SILENT_DB`` + 6 дБ). → ``[{t, before, after, ok}]``."""
    db = frame_db(pcm, FRAME, sr)
    ref = speech_level(db)
    n = max(1, int(win / FRAME))
    out = []
    for t in cuts:
        k = int(round(t / FRAME))
        b = float(db[max(0, k - n):k].max()) - ref if k > 0 else -100.0
        a = float(db[k:k + n].max()) - ref if k < len(db) else -100.0
        out.append({"t": round(t, 3), "before": round(b, 1), "after": round(a, 1),
                    "ok": max(a, b) < SILENT_DB + 6.0})
    return out


def _lag(a: Any, b: Any, max_lag: int) -> tuple[int, float]:
    """Сдвиг b относительно a (окна), при котором огибающие совпадают лучше всего: (сдвиг, корреляция)."""
    np = _np()
    best = (0, -2.0)
    for lag in range(-max_lag, max_lag + 1):
        x = a[max(0, -lag): len(a) - max(0, lag)]
        y = b[max(0, lag): len(b) - max(0, -lag)]
        k = min(len(x), len(y))
        if k < 20:
            continue
        x, y = x[:k], y[:k]
        if x.std() < 1e-6 or y.std() < 1e-6:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best[1]:
            best = (lag, c)
    return best


def voice_in_mix(voice: Any, mix: Any, lead: float, *, sr: int = SR, extra: list[tuple[float, float]] | None = None) -> dict:
    """Голос (дорожка рассказчика, начинается в ``lead`` с) против готового звука ролика.

    * ``dropouts`` — отрезки речи (≥ ``DROP_MIN_SEC``), где в сведении голос тише обычного на ``DROP_DB`` и больше:
      пропал кусок, обрезался хвост, стык отрезал слово;
    * ``sync_ms`` — сдвиг голоса в ролике относительно ожидаемого в начале, середине и конце (уезжает — рассинхрон;
      больше ``SYNC_MAX_MS`` — ошибка).
    ``extra`` — отрезки других голосов (реплики), их в проверке провалов не считаем."""
    np = _np()
    a = frame_db(voice, 0.01, sr)
    off = int(round(lead / 0.01))
    b = frame_db(mix, 0.01, sr)[off: off + len(a)]
    k = min(len(a), len(b))
    a, b = a[:k], b[:k]
    ref = speech_level(a)
    speech = a > ref - 18.0
    for t0, t1 in extra or []:
        speech[max(0, int((t0 - lead) / 0.01)): max(0, int((t1 - lead) / 0.01))] = False
    diff = b - a
    med = float(np.median(diff[speech])) if speech.any() else 0.0
    low = speech & (diff < med - DROP_DB)
    drops, start = [], None
    for i, v in enumerate(list(low) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if (i - start) * 0.01 >= DROP_MIN_SEC:
                drops.append({"t": round(lead + start * 0.01, 2), "dur": round((i - start) * 0.01, 2),
                              "db": round(float((diff[start:i] - med).mean()), 1)})
            start = None
    sync = []
    n = len(a)
    half = min(300, int(n * 0.12))  # окно ±3 с (у короткой озвучки — меньше)
    for c in (0.15, 0.5, 0.85):
        w0, w1 = int(n * c) - half, int(n * c) + half
        if w0 < 0 or w1 > n:
            continue
        lag, corr = _lag(np.maximum(a[w0:w1], ref - 50), np.maximum(b[w0:w1], ref - 50), 8)
        sync.append({"at": round(lead + (w0 + w1) / 2 * 0.01, 1), "ms": lag * 10, "corr": round(corr, 2)})
    return {"dropouts": drops, "sync": sync, "gain_db": round(med, 1)}


def check(narration: Any, final: Any, lead: float, *, sr: int = SR, extra: list[tuple[float, float]] | None = None,
          joins: list[dict] | None = None) -> dict:
    """Проверка звука эпизода: оборванные слова (``word_cuts``), стыки вставленных пауз (``joins`` — ``join_levels``
    шага пауз), провалы и синхронность в ролике (``voice_in_mix``).
    ``ok`` — всё чисто; пустой замер (нет голоса, нет окон синхронности) — не «ок»."""
    edges = word_cuts(narration, sr=sr)
    mix = voice_in_mix(narration, final, lead, sr=sr, extra=extra)
    problems = [f"слово оборвано тишиной: {e['t']:.2f} с ({e['db']:+.0f} дБ)" for e in edges]
    problems += [f"стык паузы не в тишине: {j['t']:.2f} с (до {j['before']:+.0f}, после {j['after']:+.0f} дБ)"
                 for j in joins or [] if not j.get("ok")]
    problems += [f"провал голоса в ролике: {d['t']:.2f} с, {d['dur']:.2f} с, {d['db']:+.0f} дБ" for d in mix["dropouts"]]
    problems += [f"рассинхрон голоса: {s['at']} с, {s['ms']} мс" for s in mix["sync"] if abs(s["ms"]) > SYNC_MAX_MS]
    if len(narration) < sr or not mix["sync"]:
        problems.append("проверка звука: нечего мерить (нет голоса или ролик короче голоса)")
    return {"ok": not problems, "problems": problems, "word_cuts": edges, "joins": len(joins or []), **mix,
            "voice_sec": round(len(narration) / sr, 2), "final_sec": round(len(final) / sr, 2)}
