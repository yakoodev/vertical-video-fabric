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
Открытия небольшие (речь, а не крик): полный рот — только на ударном (самом громком и длинном) слоге слова,
безударные слоги и короткие служебные слова — заметно меньше (``syllable_gain``).

Брови на ударениях (``emphasis``): громкие слова, выделенные цветом в субтитрах и вопросы → ``timeline.brows``.
Эмоции реплик плана (``emotion_faces``) → ключи ``timeline.faces``; лицо собирает node/vrm/src/facemix.ts.
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
# Предел открытия по гласной: «а» — шире всех, «и» — щель. Речь, а не крик: «а» не раскрывает рот до упора.
VOWEL_MAX = {"aa": 0.76, "oh": 0.66, "ee": 0.6, "ih": 0.5, "ou": 0.56}


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


def _phone_spans(w: dict, n: int, hop_s: float) -> tuple[int, int, list[tuple[str, dict[str, float], int, int]]]:
    """Слово → (первый кадр, конец, звуки с кадрами [j0, j1)); гласная длиннее согласной."""
    ph = phones(str(w.get("word", "")))
    a, b = float(w["start"]), float(w["end"])
    i0, i1 = max(0, int(a / hop_s)), min(n, int(math.ceil(b / hop_s)))
    if i1 <= i0 or not ph:
        return i0, i1, []
    dur = np.array([2.2 if k == "V" else 1.0 for k, _ in ph])
    edges = np.concatenate([[0.0], np.cumsum(dur) / dur.sum()]) * (i1 - i0) + i0
    out = []
    for (k, v), e0, e1 in zip(ph, edges[:-1], edges[1:]):
        j0 = int(round(e0))
        out.append((k, v, j0, min(n, max(j0 + 1, int(round(e1))))))
    return i0, i1, out


def text_track(words: list[dict], n: int, hop_s: float = HOP / SR) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Разметка по тексту на кадрах 10 мс: веса гласных (n, 5), класс звука (n,) и признак «внутри слова»."""
    shape = np.zeros((n, len(VOWELS)))
    klass = np.full(n, "", dtype=object)
    inside = np.zeros(n, bool)
    for w in words:
        i0, i1, spans = _phone_spans(w, n, hop_s)
        if i1 <= i0:
            continue
        inside[i0:i1] = True
        for k, v, j0, j1 in spans:
            klass[j0:j1] = k
            for name, val in v.items():
                shape[j0:j1, VOWELS.index(name)] = val
    return shape, klass, inside


def syllable_gain(words: list[dict], level: np.ndarray, hop_s: float = HOP / SR) -> np.ndarray:
    """Множитель открытия по слогам: ударный слог слова (громкий и долгий) — 1, прочие — 0.55–0.85 по громкости;
    короткие служебные слова (одна гласная, до 2 букв: «и», «в», «на») — 0.7. Без слов — 1."""
    n = len(level)
    g = np.ones(n)
    for w in words:
        _i0, _i1, spans = _phone_spans(w, n, hop_s)
        vow = [(j0, j1) for k, _v, j0, j1 in spans if k == "V" and j1 > j0]
        if not vow:
            continue
        lv = np.array([float(level[j0:j1].mean()) for j0, j1 in vow])
        score = lv * np.sqrt([j1 - j0 for j0, j1 in vow])
        top = int(np.argmax(score))
        peak = max(float(lv.max()), 1e-6)
        letters = sum(c.isalpha() for c in str(w.get("word", "")))
        for i, (j0, j1) in enumerate(vow):
            if len(vow) == 1:
                k = 0.7 if letters <= 2 else 0.9
            else:
                k = 1.0 if i == top else 0.55 + 0.3 * min(1.0, lv[i] / peak)
            g[j0:j1] = k
        # Согласные слова — в силе ближайшего слога (рот между слогами не «раскрывается заново»).
        for k_, _v, j0, j1 in spans:
            if k_ != "V":
                near = min(vow, key=lambda s: abs((s[0] + s[1]) / 2 - (j0 + j1) / 2))
                g[j0:j1] = g[near[0]]
    return g


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
    # Открытость: громкость × ударение слога; губы сомкнуты (м/б/п), прикрыты (в/ф), вытянуты (ш/ж/щ/ч).
    open_ = level * syllable_gain(words, level)
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
    # Коартикуляция: форма губ перетекает между звуками плавнее, чем открытие челюсти.
    shape = _smooth(shape, 0.045, 0.07, dt)
    open_ = _smooth(open_, 0.03, 0.07, dt)
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



def _norm_word(w: str) -> str:
    return "".join(c for c in w.lower().replace("ё", "е") if c.isalnum())


def emphasis(pcm: np.ndarray, sr: int, words: list[dict], *, highlight: Any = None, min_gap: float = 1.3,
             max_rate: float = 0.45) -> list[dict]:
    """Брови на ударениях: [{t — начало слова, dur — длина, amp}] для ``timeline.brows`` (node facemix.browsAt).

    Кандидаты: слова из ``highlight`` (выделены цветом в субтитрах) — 0.8; последнее слово вопроса или восклицания
    — 0.7; громкие слова (громкость слова выше средней по реплике на 0.8 разброса и длиннее 3 букв) — 0.4–0.65.
    Жадно по силе, не чаще одного на ``min_gap`` с и не больше ``max_rate`` в секунду речи. Детерминированно.
    """
    if not words:
        return []
    _mfcc, db = features(pcm, sr)
    hop = HOP / SR
    hi = {_norm_word(str(k)) for k in (highlight or [])}
    lv = []
    for w in words:
        i0, i1 = max(0, int(float(w["start"]) / hop)), min(len(db), int(math.ceil(float(w["end"]) / hop)))
        seg = np.sort(db[i0:i1])[-3:] if i1 > i0 else np.array([-120.0])
        lv.append(float(seg.mean()))
    arr = np.array(lv)
    med, spread = float(np.median(arr)), float(arr.std()) + 1.0
    cand: list[tuple[float, int]] = []
    for i, w in enumerate(words):
        text = str(w.get("word", ""))
        letters = sum(c.isalpha() for c in text)
        z = (lv[i] - med) / spread
        amp = 0.0
        if _norm_word(text) in hi:
            amp = 0.8
        if text.rstrip("»\"')").endswith(("?", "!")):
            amp = max(amp, 0.7)
        if z > 0.8 and letters >= 4:
            amp = max(amp, 0.4 + 0.25 * min(1.0, (z - 0.8) / 1.5))
        if amp > 0:
            cand.append((amp, i))
    span = float(words[-1]["end"]) - float(words[0]["start"])
    limit = max(1, int(span * max_rate + 0.5))
    kept: list[int] = []
    for amp, i in sorted(cand, key=lambda x: (-x[0], x[1])):
        t = float(words[i]["start"])
        if len(kept) >= limit or any(abs(t - float(words[j]["start"])) < min_gap for j in kept):
            continue
        kept.append(i)
    out = []
    for i in sorted(kept):
        w = words[i]
        amp = next(a for a, j in cand if j == i)
        out.append({"t": round(float(w["start"]), 3), "dur": round(float(w["end"]) - float(w["start"]), 3), "amp": round(amp, 3)})
    return out


# Эмоция реплики плана → лицо ключа faces (смесь выражений VRM; facemix раскладывает её на части модели).
EMOTION_EXPR: dict[str, dict[str, float]] = {
    "happy": {"happy": 0.55}, "smirk": {"happy": 0.3, "relaxed": 0.35}, "surprised": {"surprised": 0.75, "happy": 0.15},
    "sad": {"sad": 0.6}, "angry": {"angry": 0.6}, "neutral": {"neutral": 1.0},
}
_EMO_ALIAS = {"радость": "happy", "рада": "happy", "весело": "happy", "joy": "happy", "smile": "happy", "ухмылка": "smirk",
              "усмешка": "smirk", "smug": "smirk", "удивление": "surprised", "удивлена": "surprised", "shock": "surprised",
              "wow": "surprised", "грусть": "sad", "грустно": "sad", "печаль": "sad", "злость": "angry", "злая": "angry",
              "спокойно": "neutral", "нейтрально": "neutral", "calm": "neutral"}


def emotion_name(name: Any) -> str | None:
    """Тег эмоции (англ. или рус.) → одно из EMOTION_EXPR; неизвестный — None."""
    key = str(name or "").strip().lower()
    key = _EMO_ALIAS.get(key, key)
    return key if key in EMOTION_EXPR else None


def emotion_faces(spans: list[dict], *, k: float = 1.0) -> list[dict]:
    """Эмоции реплик [{start, end, emotion}] → ключи faces «на время» (node perf.ts hold): наплыв за 0.3 с чуть
    раньше реплики, держится до её конца, потом возврат к прежнему лицу."""
    out = []
    for sp in spans:
        emo = emotion_name(sp.get("emotion"))
        if not emo:
            continue
        a, b = float(sp["start"]), float(sp["end"])
        expr = {n: round(min(1.0, v * k), 3) for n, v in EMOTION_EXPR[emo].items()}
        out.append({"t": round(max(0.0, a - 0.15), 3), "dur": 0.3, "expr": expr, "hold": round(b - a + 0.3, 3), "out": 0.45})
    return out


def attach_timeline(timeline: dict, track: Any, words: list[dict], total: float,
                    windows: list[tuple[float, float]] | None = None, *, offset: float = 0.0,
                    life_seed: int | None = 1, highlight: Any = None, moods: list[dict] | None = None,
                    arkit: bool = False) -> bool:
    """Ролик node/vrm: рот по гласным (timeline.visemes) по голосу и словам героини, брови на ударениях
    (timeline.brows), эмоции реплик (moods → timeline.faces), плюс слой жизни (life). arkit — ещё формы ARKit от
    звука (app/wav2arkit.py, timeline.arkit) для модели с perfect sync; нет модели Wav2ARKit — без них.

    Слова и окна — во времени ролика; голос в ролике начинается с offset (вступление до первого слова). Окна речи —
    timeline.speech, если не даны. Возвращает, получилась ли дорожка гласных (иначе рот по громкости).
    """
    if moods:
        timeline["faces"] = sorted([*(timeline.get("faces") or []), *emotion_faces(moods)], key=lambda f: float(f["t"]))
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
        if arkit and "arkit" not in timeline:
            from app import wav2arkit

            ak = wav2arkit.from_track(track, offset=offset)
            if ak:
                timeline["arkit"] = mask_arkit(ak, win) if win else ak
        if "brows" not in timeline:
            brows = brows_from_track(track, local, highlight)
            if brows:
                timeline["brows"] = [{**b, "t": round(b["t"] + offset, 3)} for b in brows]
    return bool(vis)


def mask_arkit(ak: dict, windows: list[tuple[float, float]], *, fade: float = 0.12) -> dict:
    """Дорожка ARKit (время кадра = start + i/fps) только в окнах речи героя: голос рассказчика вне окон не двигает
    ей рот. На краях окна — наплыв ``fade`` с; вне окон кадр нулевой."""
    fps = float(ak.get("fps") or 30)
    start = float(ak.get("start") or 0.0)
    out = []
    for i, row in enumerate(ak.get("frames") or []):
        t = start + i / fps
        k = 0.0
        for a, b in windows:
            if a - fade <= t <= b + fade:
                k = max(k, min(1.0, (t - (a - fade)) / fade, ((b + fade) - t) / fade))
        out.append([round(v * k, 3) for v in row] if k < 1.0 else row)
    return {**ak, "frames": out}


def brows_from_track(path: Any, words: list[dict], highlight: Any = None) -> list[dict]:
    """Файл голоса → брови на ударениях (emphasis); не вышло — пусто."""
    from app import series3d_voice as VF

    try:
        return emphasis(VF.decode(path), VF.SR, words, highlight=highlight)
    except Exception:  # noqa: BLE001 - брови — украшение
        return []


def attach_psx(spec: dict, track: Any, words: list[dict], length: float) -> bool:
    """Мир PS1 (node/vrm/src/psx/world.ts): куски рта героини (spec.mouth, голос от t0) — гласные по её словам."""
    vis = from_track(track, words, length) if words else None
    if not vis:
        return False
    for m in spec.get("mouth") or []:
        m.setdefault("visemes", vis)
    return True