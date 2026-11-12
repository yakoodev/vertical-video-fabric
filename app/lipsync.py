"""Рот по гласным (липсинк) для VRM-героини: голос + текст реплик → веса выражений aa / ih / ou / ee / oh по кадрам.

Метод — как у uLipSync / wLipSync (hecomi, mrxz; MIT): кадры голоса → MFCC (пред-усиление, окно Хэмминга, БПФ,
мел-фильтры, логарифм, DCT), гласная — по близости MFCC кадра к «профилю» гласных. Перенесено на numpy: рендер у нас
покадровый и офлайн (wLipSync работает в браузере в реальном времени через AudioWorklet), без бинарников в образе.
Rhubarb (MIT) не взят: его распознаватель английский, для русского — только «фонетический» режим, и это ещё
~87 МБ на платформу.

Профиль — свой у каждого голоса, по тексту реплики (у нас он всегда есть): русские буквы почти однозначно дают
гласные (а/я → aa, о/ё → oh, у/ю → ou, и/ы → ih, е/э → ee), слова стоят по времени (Whisper или по длине текста),
внутри слова звуки разложены по долям (гласная длиннее согласной). Кадры, где по тексту гласная и голос звучит,
дают средний MFCC этой гласной у этого голоса; дальше каждый кадр классифицируется по звуку (мягкий выбор), а
текст — подсказка: итог = смесь «что слышно» и «что написано». Так безударные «о → а» и сдвиги таймингов берёт звук.

Поверх: открытость рта — по громкости (как старый рот), губы смыкаются на м/б/п, полуоткрыты на в/ф, вытянуты на
ш/ж/щ/ч; коартикуляция — губы готовят следующую гласную заранее (~50 мс), атака быстрее отпускания; сглаживание.
Выход — ``{"fps", "start", "aa", "ih", "ou", "ee", "oh"}`` для ``timeline.visemes`` node/vrm (src/lipsync.ts).
Нет слов — None (рот по громкости, ``mouth_envelope``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

VOWELS = ("aa", "ih", "ou", "ee", "oh")
SR = 16000
HOP = 160  # 10 мс
WIN = 400  # 25 мс
NFFT = 512
N_MEL = 26
N_MFCC = 12

# Буква → (класс, веса гласных). Класс: V — гласная, L — губы сомкнуты, D — губа к зубам, R — губы вперёд, C — прочие.
_RU_V = {
    "а": {"aa": 1.0}, "я": {"aa": 0.8, "ee": 0.2}, "о": {"oh": 0.65, "aa": 0.35}, "ё": {"oh": 0.9, "ou": 0.1},
    "у": {"ou": 1.0}, "ю": {"ou": 0.85, "ih": 0.15}, "ы": {"ih": 0.8, "ee": 0.2}, "и": {"ih": 1.0},
    "е": {"ee": 0.75, "ih": 0.25}, "э": {"ee": 1.0},
}
_EN_V = {"a": {"aa": 1.0}, "o": {"oh": 1.0}, "u": {"ou": 1.0}, "e": {"ee": 1.0}, "i": {"ih": 1.0}, "y": {"ih": 0.7}}
_LAB = set("мбпmbp")
_DEN = set("вфvf")
_ROUND = set("шжщчwq")
_SKIP = set("ьъ'-")
DIGIT_RU = {"0": "ноль", "1": "один", "2": "два", "3": "три", "4": "четыре", "5": "пять", "6": "шесть", "7": "семь",
            "8": "восемь", "9": "девять"}
# Предел открытия по гласной: «а» — шире всех, «и» — щель.
VOWEL_MAX = {"aa": 0.9, "oh": 0.78, "ee": 0.62, "ih": 0.5, "ou": 0.62}


def phones(word: str) -> list[tuple[str, dict[str, float]]]:
    """Слово → звуки (класс, веса гласных). Цифры — словами (порядок звуков важнее точного числа)."""
    w = word.lower()
    w = "".join(DIGIT_RU.get(c, c) for c in w)
    out: list[tuple[str, dict[str, float]]] = []
    for c in w:
        if c in _SKIP or not c.isalpha():
            continue
        if c in _RU_V:
            out.append(("V", _RU_V[c]))
        elif c in _EN_V and not ("а" <= c <= "я"):
            out.append(("V", _EN_V[c]))
        elif c in _LAB:
            out.append(("L", {}))
        elif c in _DEN:
            out.append(("D", {}))
        elif c in _ROUND:
            out.append(("R", {}))
        else:
            out.append(("C", {}))
    return out


def text_track(words: list[dict], n: int, hop_s: float = HOP / SR) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Разметка по тексту на кадрах 10 мс: веса гласных (n, 5), класс звука (n,) и признак «внутри слова»."""
    shape = np.zeros((n, len(VOWELS)))
    klass = np.full(n, "", dtype=object)
    inside = np.zeros(n, bool)
    for w in words:
        ph = phones(str(w.get("word", "")))
        a, b = float(w["start"]), float(w["end"])
        i0, i1 = max(0, int(a / hop_s)), min(n, int(math.ceil(b / hop_s)))
        if i1 <= i0:
            continue
        inside[i0:i1] = True
        if not ph:
            continue
        dur = np.array([2.2 if k == "V" else 1.0 for k, _ in ph])
        edges = np.concatenate([[0.0], np.cumsum(dur) / dur.sum()]) * (i1 - i0) + i0
        for (k, v), e0, e1 in zip(ph, edges[:-1], edges[1:]):
            j0, j1 = int(round(e0)), max(int(round(e0)) + 1, int(round(e1)))
            klass[j0:j1] = k
            for name, val in v.items():
                shape[j0:j1, VOWELS.index(name)] = val
    return shape, klass, inside


def _mel_bank(sr: int = SR, nfft: int = NFFT, n: int = N_MEL) -> np.ndarray:
    def hz2mel(f: float) -> float:
        return 1127.0 * math.log(1 + f / 700.0)

    mels = np.linspace(hz2mel(60), hz2mel(sr / 2 * 0.95), n + 2)
    hz = 700.0 * (np.exp(mels / 1127.0) - 1)
    bins = np.floor((nfft + 1) * hz / sr).astype(int)
    fb = np.zeros((n, nfft // 2 + 1))
    for m in range(1, n + 1):
        a, c, b = bins[m - 1], bins[m], bins[m + 1]
        for k in range(a, c):
            fb[m - 1, k] = (k - a) / max(1, c - a)
        for k in range(c, b):
            fb[m - 1, k] = (b - k) / max(1, b - c)
    return fb


def features(pcm: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Голос → (MFCC 1..12 по кадрам 10 мс, громкость дБ по кадрам)."""
    x = np.asarray(pcm, dtype=np.float64)
    if sr != SR:  # грубая, но достаточная передискретизация (речь до 8 кГц)
        n = int(len(x) * SR / sr)
        x = np.interp(np.arange(n) * (sr / SR), np.arange(len(x)), x)
    x = np.append(x[0] if len(x) else 0.0, x[1:] - 0.97 * x[:-1]) if len(x) else x
    n = max(1, 1 + (len(x) - WIN) // HOP) if len(x) >= WIN else 1
    pad = np.pad(x, (0, max(0, (n - 1) * HOP + WIN - len(x))))
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = pad[idx] * np.hamming(WIN)[None, :]
    spec = np.abs(np.fft.rfft(frames, NFFT)) ** 2
    mel = np.log(spec @ _mel_bank().T + 1e-10)
    k = np.arange(N_MEL)
    dct = np.cos(np.pi / N_MEL * (k[None, :] + 0.5) * np.arange(1, N_MFCC + 1)[:, None])
    mfcc = mel @ dct.T
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    return mfcc, db


def calibrate(mfcc: np.ndarray, shape: np.ndarray, voiced: np.ndarray, min_frames: int = 6) -> dict[str, np.ndarray]:
    """Профиль голоса: средний MFCC каждой гласной по кадрам, где текст уверенно даёт её и голос звучит."""
    prof: dict[str, np.ndarray] = {}
    for j, v in enumerate(VOWELS):
        sel = voiced & (shape[:, j] >= 0.75)
        if sel.sum() >= min_frames:
            prof[v] = mfcc[sel].mean(axis=0)
    return prof


def classify(mfcc: np.ndarray, prof: dict[str, np.ndarray], temp: float = 0.35) -> np.ndarray:
    """Мягкий выбор гласной по близости к профилю (как uLipSync: обратное расстояние → доли)."""
    out = np.zeros((len(mfcc), len(VOWELS)))
    if len(prof) < 2:
        return out
    names = list(prof)
    cent = np.stack([prof[n] for n in names])
    # Расстояние в нормированных осях (разброс коэффициентов разный).
    scale = mfcc.std(axis=0) + 1e-6
    d = np.linalg.norm((mfcc[:, None, :] - cent[None, :, :]) / scale, axis=2)
    d = d / (np.median(d, axis=1, keepdims=True) + 1e-9)
    w = np.exp(-d / temp)
    w /= w.sum(axis=1, keepdims=True)
    for i, n in enumerate(names):
        out[:, VOWELS.index(n)] = w[:, i]
    return out


def _smooth(x: np.ndarray, tau_up: float, tau_down: float, dt: float) -> np.ndarray:
    """Сглаживание с разной скоростью нарастания и спада (по кадрам, вперёд)."""
    y = np.empty_like(x)
    a_up, a_dn = 1 - math.exp(-dt / tau_up), 1 - math.exp(-dt / tau_down)
    acc = x[0]
    for i in range(len(x)):
        a = np.where(x[i] > acc, a_up, a_dn)
        acc = acc + (x[i] - acc) * a
        y[i] = acc
    return y


def visemes(pcm: np.ndarray, sr: int, words: list[dict], total: float, *, windows: list[tuple[float, float]] | None = None,
            fps: int = 30, audio_share: float = 0.4, lead: float = 0.05) -> dict[str, Any] | None:
    """Голос и слова → дорожка гласных для node/vrm (кадры fps на [0, total]). Нет слов — None.

    windows — где говорит героиня (вне окон рот закрыт; у реплик других героев — тоже). audio_share — доля «что
    слышно» в форме рта (остальное — «что написано»), lead — на сколько губы опережают звук (коартикуляция).
    """
    if not words:
        return None
    mfcc, db = features(pcm, sr)
    n = len(db)
    shape_t, klass, inside = text_track(words, n)
    ref = np.percentile(db[inside], 95) if inside.any() else db.max()
    level = np.clip((db - (ref - 32)) / 26, 0, 1)  # 0 — тишина (−32 дБ от громкой речи), 1 — громко
    voiced = level > 0.35
    prof = calibrate(mfcc, shape_t, voiced & inside)
    audio = classify(mfcc, prof)
    vowel_t = klass == "V"
    # Форма: гласная по тексту там, где текст её даёт, иначе — ближайшая (согласные — в форме соседней гласной).
    shape = shape_t.copy()
    last = np.zeros(len(VOWELS))
    for i in range(n):  # вперёд: «держать» гласную через согласные
        if vowel_t[i]:
            last = shape_t[i]
        elif inside[i]:
            shape[i] = last * 0.6
    nxt = np.zeros(len(VOWELS))
    for i in range(n - 1, -1, -1):  # назад: согласная перед гласной уже в её форме
        if vowel_t[i]:
            nxt = shape_t[i]
        elif inside[i]:
            shape[i] = np.maximum(shape[i], nxt * 0.7)
    if prof:
        k = audio_share * voiced[:, None]
        shape = (1 - k) * shape + k * audio
    s = shape.sum(axis=1, keepdims=True)
    shape = np.where(s > 1e-6, shape / np.maximum(s, 1e-6), 0)
    # Открытость: громкость; губы сомкнуты (м/б/п), прикрыты (в/ф), вытянуты (ш/ж/щ/ч).
    open_ = level.copy()
    open_[klass == "L"] *= 0.0
    open_[klass == "D"] *= 0.35
    open_[klass == "C"] *= 0.55  # согласная между гласными — рот прикрывается: слоги «пульсируют»
    open_[~inside] *= 0.15
    rnd = klass == "R"
    shape[rnd] = 0.5 * shape[rnd] + 0.5 * np.eye(len(VOWELS))[VOWELS.index("ou")]
    open_[rnd] *= 0.6
    if windows is not None:
        t = np.arange(n) * HOP / SR
        mask = np.zeros(n, bool)
        for a, b in windows:
            mask |= (t >= a - 0.05) & (t <= b + 0.08)
        open_[~mask] = 0
    dt = HOP / SR
    shape = _smooth(shape, 0.035, 0.06, dt)
    open_ = _smooth(open_, 0.03, 0.065, dt)
    # Смыкание губ (м/б/п) — после сглаживания и до конца: короткий звук иначе тонет в соседних гласных.
    lab = (klass == "L").astype(float)
    lab = np.maximum(lab, np.maximum(np.roll(lab, 1), np.roll(lab, -1)))
    lab = np.maximum(_smooth(lab, 0.012, 0.02, dt), _smooth(lab[::-1], 0.012, 0.02, dt)[::-1])
    open_ = open_ * (1 - np.clip(lab, 0, 1) * 0.92)
    peak = np.array([VOWEL_MAX[v] for v in VOWELS])
    w = shape * open_[:, None] * peak[None, :]
    # В кадры видео; губы — на lead раньше звука.
    m = int(math.ceil(total * fps)) + 1
    tt = np.arange(m) / fps + lead
    src = np.arange(n) * dt
    out: dict[str, Any] = {"fps": fps, "start": 0, "lead": lead}
    for j, v in enumerate(VOWELS):
        out[v] = [round(float(x), 3) for x in np.clip(np.interp(tt, src, w[:, j], left=0, right=0), 0, 1)]
    out["profile"] = sorted(prof)
    return out


def from_track(path: Any, words: list[dict], total: float, windows: list[tuple[float, float]] | None = None,
               fps: int = 30) -> dict[str, Any] | None:
    """Файл голоса (любой формат ffmpeg) → дорожка гласных; не вышло — None (рот по громкости)."""
    from app import series3d_voice as VF

    try:
        pcm = VF.decode(path)
        return visemes(pcm, VF.SR, words, total, windows=windows, fps=fps)
    except Exception:  # noqa: BLE001 - липсинк — улучшение: без него рот по громкости
        return None


def agreement(vis: dict[str, Any], words: list[dict]) -> dict[str, float]:
    """Насколько рот совпал с текстом: доля кадров-гласных (по тексту), где самая сильная форма — та же гласная,
    и доля кадров м/б/п, где рот почти закрыт. Для проверок и отчёта."""
    fps = vis["fps"]
    m = len(vis["aa"])
    W = np.stack([np.array(vis[v]) for v in VOWELS], 1)
    hit = tot = closed = labs = 0
    for w in words:
        ph = phones(str(w.get("word", "")))
        if not ph:
            continue
        a, b = float(w["start"]), float(w["end"])
        dur = np.array([2.2 if k == "V" else 1.0 for k, _ in ph])
        edges = a + np.concatenate([[0.0], np.cumsum(dur) / dur.sum()]) * (b - a)
        for (k, v), e0, e1 in zip(ph, edges[:-1], edges[1:]):
            mid = int(round(((e0 + e1) / 2 - float(vis.get("lead") or 0)) * fps))
            if not 0 <= mid < m:
                continue
            if k == "V" and max(v.values()) >= 0.75 and W[mid].sum() > 0.08:
                tot += 1
                hit += int(VOWELS[int(np.argmax(W[mid]))] == max(v, key=v.get))
            elif k == "L":
                labs += 1
                closed += int(W[mid].sum() < 0.25)
    return {"vowel_match": round(hit / tot, 3) if tot else 0.0, "vowels": tot,
            "lips_closed": round(closed / labs, 3) if labs else 0.0, "labials": labs}



def attach_timeline(timeline: dict, track: Any, words: list[dict], total: float,
                    windows: list[tuple[float, float]] | None = None, *, offset: float = 0.0,
                    life_seed: int | None = 1) -> bool:
    """Ролик node/vrm: рот по гласным (timeline.visemes) по голосу и словам героини, плюс слой жизни (life).

    Слова и окна — во времени ролика; голос в ролике начинается с offset (вступление до первого слова). Окна речи —
    timeline.speech, если не даны. Возвращает, получилась ли дорожка гласных (иначе рот по громкости).
    """
    if life_seed is not None and "life" not in timeline:
        timeline["life"] = {"seed": int(life_seed)}
    win = windows if windows is not None else [tuple(w) for w in timeline.get("speech") or []]
    inside = [w for w in words if any(a - 0.05 <= float(w["start"]) <= b + 0.05 for a, b in win)] if win else words
    if not inside:
        return False
    local = [{**w, "start": float(w["start"]) - offset, "end": float(w["end"]) - offset} for w in inside]
    vis = from_track(track, local, max(0.1, total - offset), windows=[(a - offset, b - offset) for a, b in win] or None)
    if vis:
        vis["start"] = round(offset, 3)
        timeline["visemes"] = vis
    return bool(vis)


def attach_psx(spec: dict, track: Any, words: list[dict], length: float) -> bool:
    """Мир PS1 (node/vrm/src/psx/world.ts): куски рта героини (spec.mouth, голос от t0) — гласные по её словам."""
    vis = from_track(track, words, length) if words else None
    if not vis:
        return False
    for m in spec.get("mouth") or []:
        m.setdefault("visemes", vis)
    return True