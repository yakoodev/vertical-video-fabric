"""Текст песни → строки со временем слов и слогов (чистые функции, без медиа и моделей).

Зачем (плейбук frag-movie, журнал монтажа 15.11): текст песни в мувике — «аналог субтитров»: ВСЕ спетые строки
целиком, по слогам вокала. Раньше это делали скрипты вне сервиса (``tools/music_video``, ``movie5/scripts``):
Whisper по вокальному стему — какие слова и где примерно, атаки слогов на стеме — точное время. Здесь то же как
библиотека сервиса; звук, demucs и Whisper — ``app/lyrics_audio.py``, инструмент — ``app/lyrics_tools.py``.

Три шага:

1. ``parse_lyrics(text)`` — строки текста владельца: заголовки секций ([Chorus], «Припев:») выкидываются, «(x2)» в
   конце строки — повтор строки, пустые строки — границы куплетов (не строки).
2. ``align_text(lines, heard)`` — глобальное выравнивание (Нидлман — Вунш) слов текста со словами Whisper: слово
   текста получает время услышанного слова (``how: heard``, похожее — ``approx``); не услышанное — время между
   соседями по слогам (``interp``). Строка без единого услышанного слова — ``unsure`` (сомнительную строку плейбук
   велит не показывать). Без текста — ``lines_from_heard`` (строки по паузам и знакам Whisper).
3. ``snap_to_onsets(lines, onsets)`` — начало слова — к ближайшей атаке слога на вокальном стеме, слоги слова — атаки
   внутри него (``syl``); ``vocal_onsets`` — атаки (спектральный поток 120–6000 Гц, шаг 5 мс; как tools/music_video).

Начала слов, которые Whisper поставил в тишину вокала (растянутое первое слово фразы), — ``fix_silent_starts``.

Время везде — секунды трека (песни), а не ролика: перевод в ролик — ``app/lyrics_layers.py``.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable

VOWELS = set("аеёиоуыэюяaeiouyæøåäöü")
SECTION_RE = re.compile(
    r"^\s*(\[[^\]]*\]|\{[^}]*\}|(куплет|припев|бридж|интро|аутро|проигрыш|verse|chorus|pre-?chorus|bridge|intro|"
    r"outro|hook|refrain)\b[^a-zа-яё]*\d*\s*:?\s*$)", re.I)
REPEAT_RE = re.compile(r"[\s(\[]*(?:[xх×*]\s*(\d{1,2})|(\d{1,2})\s*[xх×])[\s)\]]*$", re.I)
_PUNCT_END = re.compile(r"[,.!?;:…]$")

# Выравнивание: цена шагов (больше — лучше).
MATCH_SIM = 0.72  # похожесть, с которой слова — одно и то же («gonna»/«gona», «щас»/«сейчас» — нет)
NEAR_SIM = 0.5  # похоже на то же слово, но неуверенно (approx)
GAP_LYRIC = -0.6  # слово текста не услышано
GAP_HEARD = -0.35  # лишнее услышанное слово (подпевки, выдумка Whisper)
SUB_COST = -1.0  # услышано другое слово на этом месте
MAX_CELLS = 6_000_000  # слов текста × слов Whisper: больше — режь песню на участки (start/end)

SYLLABLE_SEC = 0.22  # слог без опоры (interp) — столько секунд
SNAP_BACK = 0.20  # атака слога может быть раньше слова Whisper на столько
SNAP_FWD = 0.32  # …и позже на столько (Whisper на пении обычно спешит)
MIN_WORD_GAP = 0.06
LINE_PAUSE = 0.6  # без текста: пауза между словами, с которой начинается новая строка
LINE_MAX_WORDS = 8


def norm_word(word: Any) -> str:
    """Слово для сравнения: строчные, ё→е, без знаков и апострофов («Don't» → «dont»)."""
    text = unicodedata.normalize("NFKC", str(word or "")).lower().replace("ё", "е")
    return "".join(ch for ch in text if ch.isalnum())


def syllables(word: Any) -> int:
    return max(1, sum(ch in VOWELS for ch in str(word or "").lower()))


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        ratio = max(ratio, 0.75)  # «run»/«running», «hey»/«heyy»
    return ratio


# --------------------------------------------------------------------------- #
# Текст владельца
# --------------------------------------------------------------------------- #


def parse_lyrics(text: Any) -> list[dict]:
    """Строки текста: ``[{i, text, words: [показ], norm: [для сравнения], stanza}]``.

    Заголовки секций и пустые строки — не строки (пустая — граница куплета ``stanza``). «(x2)», «x3», «2x» в конце —
    строка повторяется столько раз (повторы плейбук велит показывать все). Слово из одних знаков («—») — к соседу."""
    out: list[dict] = []
    stanza = 0
    blank = False
    for raw in str(text or "").replace("\r", "").split("\n"):
        line = " ".join(raw.split())
        if not line:
            blank = True
            continue
        if SECTION_RE.match(line):
            blank = True
            continue
        if blank and out:
            stanza += 1
        blank = False
        repeat = 1
        match = REPEAT_RE.search(line)
        if match and match.start() > 0:
            repeat = max(1, min(8, int(match.group(1) or match.group(2))))
            line = line[:match.start()].strip()
        words: list[str] = []
        for token in line.split():
            if norm_word(token):
                words.append(token)
            elif words:
                words[-1] += " " + token
        if not words:
            continue
        for _ in range(repeat):
            out.append({"i": len(out), "text": " ".join(words), "words": list(words),
                        "norm": [norm_word(w) for w in words], "stanza": stanza})
    return out


# --------------------------------------------------------------------------- #
# Выравнивание текста со словами Whisper
# --------------------------------------------------------------------------- #


def _heard(words: Iterable[dict]) -> list[dict]:
    """Слова Whisper ``[{w|word, s|start, e|end, p?}]`` → ``[{w, n, s, e, p}]`` по времени, без пустых."""
    out = []
    for item in words or []:
        token = str(item.get("w", item.get("word", "")) or "").strip()
        n = norm_word(token)
        if not n:
            continue
        s = float(item.get("s", item.get("start", 0.0)) or 0.0)
        e = float(item.get("e", item.get("end", s)) or s)
        out.append({"w": token, "n": n, "s": round(s, 3), "e": round(max(e, s + 0.05), 3),
                    "p": round(float(item.get("p", item.get("probability", 1.0)) or 0.0), 3)})
    out.sort(key=lambda w: w["s"])
    return out


def _dp(lyric: list[str], heard: list[str]) -> list[tuple[int | None, int | None, float]]:
    """Путь выравнивания: пары (индекс слова текста | None, индекс услышанного | None, похожесть)."""
    n, m = len(lyric), len(heard)
    if n * m > MAX_CELLS:
        raise ValueError(f"слишком длинно для одного выравнивания ({n} слов текста × {m} услышанных) — "
                         "раздели песню на участки start_sec/end_sec")
    sim_cache: dict[tuple[str, str], float] = {}

    def sim(i: int, j: int) -> float:
        key = (lyric[i], heard[j])
        if key not in sim_cache:
            sim_cache[key] = similarity(*key)
        return sim_cache[key]

    def step(s: float) -> float:
        if s >= MATCH_SIM:
            return 2.0 * s
        if s >= NEAR_SIM:
            return 0.4
        return SUB_COST

    neg = float("-inf")
    score = [[neg] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 1 — диагональ, 2 — пропуск слова текста, 3 — лишнее услышанное
    score[0][0] = 0.0
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] + GAP_HEARD
        back[0][j] = 3
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + GAP_LYRIC
        back[i][0] = 2
        row, prev = score[i], score[i - 1]
        brow = back[i]
        for j in range(1, m + 1):
            best, how = prev[j - 1] + step(sim(i - 1, j - 1)), 1
            cand = prev[j] + GAP_LYRIC
            if cand > best:
                best, how = cand, 2
            cand = row[j - 1] + GAP_HEARD
            if cand > best:
                best, how = cand, 3
            row[j], brow[j] = best, how
    path: list[tuple[int | None, int | None, float]] = []
    i, j = n, m
    while i > 0 or j > 0:
        how = back[i][j]
        if how == 1:
            path.append((i - 1, j - 1, sim(i - 1, j - 1)))
            i, j = i - 1, j - 1
        elif how == 2:
            path.append((i - 1, None, 0.0))
            i -= 1
        else:
            path.append((None, j - 1, 0.0))
            j -= 1
    path.reverse()
    return path


def align_text(lines: list[dict], heard_words: Iterable[dict]) -> dict:
    """Строки текста (``parse_lyrics``) + слова Whisper → строки со временем слов.

    Ответ: ``{lines: [{i, text, t0, t1, words: [{w, t0, t1, how}], matched, unsure, stanza}], stats}``;
    ``how`` — heard (то же слово услышано), approx (похожее/другое слово на этом месте), interp (не услышано,
    время между соседями по слогам)."""
    heard = _heard(heard_words)
    flat: list[tuple[int, int]] = [(li, wi) for li, line in enumerate(lines) for wi in range(len(line["words"]))]
    lyric_norm = [lines[li]["norm"][wi] for li, wi in flat]
    timed: list[dict | None] = [None] * len(flat)
    used_heard = 0
    if heard and flat:
        for a, b, s in _dp(lyric_norm, [h["n"] for h in heard]):
            if a is None or b is None:
                continue
            how = "heard" if s >= MATCH_SIM else "approx"
            h = heard[b]
            timed[a] = {"t0": h["s"], "t1": h["e"], "how": how, "p": h["p"], "sim": round(s, 3)}
            used_heard += 1
    _fill_gaps(timed, [syllables(lines[li]["words"][wi]) for li, wi in flat])
    out_lines = []
    for li, line in enumerate(lines):
        words = []
        for k, (fl, fw) in enumerate(flat):
            if fl != li:
                continue
            t = timed[k] or {"t0": 0.0, "t1": 0.0, "how": "interp"}
            words.append({"w": line["words"][fw], "t0": round(t["t0"], 3), "t1": round(t["t1"], 3), "how": t["how"]})
        anchors = sum(1 for w in words if w["how"] == "heard")
        near = sum(1 for w in words if w["how"] == "approx")
        matched = round((anchors + 0.5 * near) / max(1, len(words)), 3)
        out_lines.append({"i": li, "text": line["text"], "stanza": line.get("stanza", 0),
                          "t0": words[0]["t0"] if words else 0.0, "t1": words[-1]["t1"] if words else 0.0,
                          "words": words, "matched": matched,
                          # Ни одного уверенно услышанного слова или меньше трети — строке верить нельзя.
                          "unsure": anchors == 0 or matched < 0.34})
    total = len(flat)
    heard_n = sum(1 for t in timed if t and t["how"] == "heard")
    return {"lines": out_lines,
            "stats": {"words": total, "heard": heard_n, "approx": sum(1 for t in timed if t and t["how"] == "approx"),
                      "interp": sum(1 for t in timed if not t or t["how"] == "interp"),
                      "heard_share": round(heard_n / total, 3) if total else 0.0,
                      "whisper_words": len(heard), "whisper_unused": max(0, len(heard) - used_heard),
                      "unsure_lines": sum(1 for line in out_lines if line["unsure"])}}


def _fill_gaps(timed: list[dict | None], weights: list[int]) -> None:
    """Неуслышанные слова — между соседями с временем, по слогам; с краёв — вплотную к соседу."""
    n = len(timed)
    k = 0
    while k < n:
        if timed[k] is not None:
            k += 1
            continue
        start = k
        while k < n and timed[k] is None:
            k += 1
        run = list(range(start, k))
        need = sum(weights[i] for i in run) * SYLLABLE_SEC
        left = timed[start - 1]["t1"] if start > 0 and timed[start - 1] else None
        right = timed[k]["t0"] if k < n and timed[k] else None
        if left is None and right is None:
            left, right = 0.0, need
        elif left is None:
            left = max(0.0, right - need)
        elif right is None:
            right = left + need
        span = max(0.0, right - left)
        # Длинная дыра (проигрыш, Whisper не расслышал куплет): слова не размазываем на весь проигрыш —
        # ставим вплотную к следующему услышанному (строка обычно поётся перед ним), с обычным темпом.
        if span > 2.5 * need and right is not None and k < n:
            left = right - need
            span = need
        t = left
        for i in run:
            share = span * weights[i] / max(1, sum(weights[j] for j in run))
            timed[i] = {"t0": round(t, 3), "t1": round(t + share, 3), "how": "interp"}
            t += share


def lines_from_heard(heard_words: Iterable[dict], max_words: int = LINE_MAX_WORDS, pause: float = LINE_PAUSE) -> dict:
    """Без текста владельца: строки из слов Whisper — по паузам ≥ ``pause``, знакам конца фразы и ``max_words``.
    Уверенность строки — средняя вероятность слов (< 0.5 — ``unsure``)."""
    heard = _heard(heard_words)
    groups: list[list[dict]] = []
    for word in heard:
        cur = groups[-1] if groups else None
        if (cur is None or word["s"] - cur[-1]["e"] >= pause or len(cur) >= max_words
                or _PUNCT_END.search(cur[-1]["w"])):
            groups.append([word])
        else:
            cur.append(word)
    lines = []
    for i, group in enumerate(groups):
        mean_p = sum(w["p"] for w in group) / len(group)
        words = [{"w": w["w"].strip(",.!?;:…"), "t0": w["s"], "t1": w["e"], "how": "heard"} for w in group]
        lines.append({"i": i, "text": " ".join(w["w"] for w in words), "stanza": 0, "t0": words[0]["t0"],
                      "t1": words[-1]["t1"], "words": words, "matched": round(mean_p, 3), "unsure": mean_p < 0.5})
    total = sum(len(g) for g in groups)
    return {"lines": lines, "stats": {"words": total, "heard": total, "approx": 0, "interp": 0,
                                      "heard_share": 1.0 if total else 0.0, "whisper_words": len(heard),
                                      "whisper_unused": 0, "unsure_lines": sum(1 for l in lines if l["unsure"])}}


# --------------------------------------------------------------------------- #
# Атаки слогов на вокале
# --------------------------------------------------------------------------- #


def vocal_onsets(samples: Any, sr: int, t0: float = 0.0, min_db: float = -38.0, gap: float = 0.07) -> list[list[float]]:
    """Атаки слогов на вокальном стеме: ``[[t, сила 0..1, dB], …]`` (t — секунды трека, ``t0`` — начало стема)."""
    return vocal_envelope(samples, sr, t0, min_db, gap)["onsets"]


def vocal_envelope(samples: Any, sr: int, t0: float = 0.0, min_db: float = -38.0, gap: float = 0.07) -> dict:
    """Атаки слогов и громкость вокального стема: ``{onsets: [[t, сила 0..1, dB], …], db: [...], fps, t0}``.

    Спектральный поток 120–6000 Гц, шаг 5 мс, адаптивный порог (как tools/music_video/audio.vocal_onsets, одобренный
    в «клипе на песню» 13.11); db — громкость кадра (окно Ханна) для ``fix_silent_starts``."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    if x.size < sr // 2:
        return {"onsets": [], "db": [], "fps": 200.0, "t0": t0}
    hop, nfft = max(1, int(round(sr * 0.005))), 2048
    pad = np.pad(x, (nfft // 2, nfft // 2))
    frames = np.lib.stride_tricks.sliding_window_view(pad, nfft)[::hop]
    window = np.hanning(nfft).astype(np.float32)
    out_flux = []
    out_db = []
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    band = (freqs > 120) & (freqs < 6000)
    prev = None
    for start in range(0, len(frames), 2048):  # кусками: длинная песня не съест память
        chunk = frames[start:start + 2048] * window
        mag = np.abs(np.fft.rfft(chunk, axis=1)).astype(np.float32)
        log = np.log1p(200 * mag[:, band])
        diff = np.diff(log, axis=0, prepend=log[:1] if prev is None else prev[None, :])
        out_flux.append(np.maximum(0, diff).sum(1))
        out_db.append(20 * np.log10(np.sqrt((chunk ** 2).mean(1)) + 1e-6))
        prev = log[-1]
    flux = np.concatenate(out_flux)
    db = np.concatenate(out_db)
    flux[0] = 0.0
    fps = sr / hop
    kernel = np.hanning(9)
    kernel /= kernel.sum()
    fl = np.convolve(flux, kernel, mode="same")
    nb = max(1, int(0.4 * fps))
    base = np.convolve(fl, np.ones(nb) / nb, mode="same")
    peaks = []
    for i in range(10, len(fl) - 10):
        if fl[i] == fl[i - 8:i + 9].max() and fl[i] > base[i] * 1.35 and db[min(len(db) - 1, i + 6)] > min_db:
            peaks.append(i)
    merged: list[int] = []
    for i in peaks:
        if merged and (i - merged[-1]) / fps < gap:
            if fl[i] > fl[merged[-1]]:
                merged[-1] = i
            continue
        merged.append(i)
    top = float(fl.max()) or 1.0
    onsets = [[round(float(i / fps + t0), 3), round(float(fl[i] / top), 3), round(float(db[min(len(db) - 1, i + 6)]), 1)]
              for i in merged]
    return {"onsets": onsets, "db": [round(float(v), 1) for v in db], "fps": float(fps), "t0": float(t0)}


def fix_silent_starts(lines: list[dict], env: dict, below_peak_db: float = 24.0, hold_sec: float = 0.06) -> dict:
    """Слово, которое по Whisper начинается в тишине вокала, — с того места, где голос зазвучал.

    Whisper на пении растягивает первое слово фразы на паузу перед ним (на замере 16.11 — до 1.6 с раньше): начало
    такого слова переносится на первый кадр после ``t0``, где стем громче порога (пик песни − ``below_peak_db``) не
    меньше ``hold_sec``, но не позже, чем за 0.08 с до конца слова. Ответ — ``{moved, words}``."""
    db = env.get("db") or []
    if not db:
        return {"moved": 0, "words": sum(len(l["words"]) for l in lines)}
    fps, base = float(env.get("fps") or 200.0), float(env.get("t0") or 0.0)
    ordered = sorted(db)
    threshold = max(-50.0, ordered[int(0.98 * (len(ordered) - 1))] - below_peak_db)
    need = max(1, int(round(hold_sec * fps)))

    def frame(t: float) -> int:
        return max(0, min(len(db) - 1, int(round((t - base) * fps))))

    moved = total = 0
    for line in lines:
        for word in line["words"]:
            total += 1
            a, b = frame(float(word["t0"])), frame(float(word["t1"]) - 0.08)
            if b <= a or sum(db[a:a + need]) / need >= threshold:
                continue
            run = 0
            for i in range(a, b + 1):
                run = run + 1 if db[i] >= threshold else 0
                if run >= need:
                    new_t = base + (i - need + 1) / fps
                    if new_t - float(word["t0"]) >= 0.05:
                        word["t0"] = round(new_t, 3)
                        word["fixed"] = "start in silence"
                        moved += 1
                    break
        if line["words"]:
            line["t0"] = line["words"][0]["t0"]
    return {"moved": moved, "words": total}


def snap_to_onsets(lines: list[dict], onsets: list[list[float]], loud_db: float = -30.0) -> dict:
    """Начало каждого слова — к атаке слога на вокале рядом со временем Whisper, по порядку (атака не достаётся двум
    словам); слоги слова — следующие атаки внутри него (``syl``). interp-слова сдвигаются вместе с соседями только
    если атака рядом нашлась. Меняет ``lines`` на месте; ответ — сводка ``{snapped, words, mean_shift}``."""
    if not onsets:
        return {"snapped": 0, "words": sum(len(l["words"]) for l in lines), "mean_shift": 0.0}
    import bisect

    times = [o[0] for o in onsets]
    used: set[int] = set()
    prev_t = -1.0
    snapped = 0
    shifts: list[float] = []
    total = 0
    for line in lines:
        for k, word in enumerate(line["words"]):
            total += 1
            t = float(word["t0"])
            lo, hi = max(prev_t + MIN_WORD_GAP, t - SNAP_BACK), t + SNAP_FWD
            a, b = bisect.bisect_left(times, lo), bisect.bisect_right(times, hi)
            cand = [i for i in range(a, b) if i not in used]
            loud = [i for i in cand if onsets[i][2] >= loud_db]
            cand = loud or cand
            if cand:
                # Whisper на пении обычно чуть раньше атаки: ближе к t + 0.05, сильная атака — плюс.
                best = min(cand, key=lambda i: abs(times[i] - (t + 0.05)) - 0.08 * onsets[i][1])
                new_t = times[best]
                used.add(best)
                shifts.append(new_t - t)
                end = max(float(word["t1"]), new_t + 0.08)
                nxt = line["words"][k + 1]["t0"] if k + 1 < len(line["words"]) else end + 1.0
                limit = min(end, float(nxt) - 0.02) if float(nxt) > new_t else end
                syl_ids: list[int] = []
                for i in range(best + 1, len(times)):
                    if times[i] >= limit or len(syl_ids) >= syllables(word["w"]) - 1:
                        break
                    if i not in used:
                        syl_ids.append(i)
                used.update(syl_ids)
                syl = [times[i] for i in syl_ids]
                word["t0"] = round(new_t, 3)
                word["t1"] = round(max(end, new_t + 0.08), 3)
                word["syl"] = [round(new_t, 3), *[round(s, 3) for s in syl]]
                word["snap"] = round(new_t - t, 3)
                snapped += 1
            prev_t = float(word["t0"])
        if line["words"]:
            line["t0"], line["t1"] = line["words"][0]["t0"], max(w["t1"] for w in line["words"])
    return {"snapped": snapped, "words": total,
            "mean_shift": round(sum(abs(s) for s in shifts) / len(shifts), 3) if shifts else 0.0}


def compact_lines(lines: list[dict], with_words: bool = False) -> list[dict]:
    """Строки для ответа агенту: время строки и начала слов (``at``, по порядку слов ``text``), сомнительность."""
    out = []
    for line in lines:
        row = {"i": line["i"], "text": line["text"], "t0": line["t0"], "t1": line["t1"],
               "at": [w["t0"] for w in line["words"]], "matched": line["matched"]}
        if line.get("unsure"):
            row["unsure"] = True
        if with_words:
            row["words"] = line["words"]
        out.append(row)
    return out


__all__ = ["align_text", "compact_lines", "fix_silent_starts", "lines_from_heard", "norm_word", "parse_lyrics",
           "similarity", "snap_to_onsets", "syllables", "vocal_envelope", "vocal_onsets"]
