"""Сведение: анализ уровня и маскировки (из ``app/audio_mix.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.audio_mix_model import (
    SILENT, LAYER_DB_RANGE, ebur128, db2p, p2db, power_mean, power_sum, mix_wavs, item_peak_lufs
)

STEMS = ("voice", "source", "music", "sfx", "video")
STEM_LABELS = {"voice": "голос (слои)", "source": "звук исходника", "music": "музыка", "sfx": "звуки",
               "video": "звук видео-слоёв"}


FRAME_SEC = 0.1
TARGET_LUFS = -14.0
TRUE_PEAK_MAX_DB = -1.0
LOUDNESS_TOLERANCE_LU = 1.0
# Музыка под речью: не меньше MUSIC_UNDER_MIN_DB тише голоса (иначе «громко»), цель — 15–18.
MUSIC_UNDER_MIN_DB = 12.0
MUSIC_UNDER_GOOD_DB = 15.0
MUSIC_UNDER_TARGET_DB = 18.0
MUSIC_UNDER_MAX_DB = 30.0  # тише — музыку под речью почти не слышно
# Звук видео-слоя под речью (фон сцены): не громче речи − этого.
VIDEO_UNDER_MIN_DB = 10.0
# Звук «закрывает» слово: под речью громче голоса минус этот запас.
SFX_MASK_DB = 3.0
# Звук слоя громче речи больше чем на столько (дБ) — «звуки громче голоса».
SFX_OVER_VOICE_DB = 2.0
# Речь — кадры, где голос+исходник не тише их уровня больше чем на столько (LU), и не тише −50.
SPEECH_BELOW_LEVEL_LU = 12.0
SPEECH_FLOOR_LUFS = -50.0
SPEECH_MERGE_SEC = 0.35
SPEECH_CORE_LU = 6.0
SILENCE_LUFS = -50.0
SILENCE_MIN_SEC = 1.0
MAX_RANGES = 6
# Лимитер финального прохода: срезать больше — громкость «качает», удары плющатся.
LIMITER_HEAVY_DB = 3.0


# --------------------------------------------------------------------------- #
# Анализ
# --------------------------------------------------------------------------- #


def _ranges(mask: list[bool], merge: float = SPEECH_MERGE_SEC, step: float = FRAME_SEC,
            shift: float = 0.0) -> list[tuple[float, float]]:
    """Подряд идущие кадры маски → отрезки (с), дырки короче ``merge`` склеены."""
    out: list[list[float]] = []
    for i, on in enumerate(mask):
        if not on:
            continue
        a = max(0.0, i * step + shift)
        b = a + step
        if out and a - out[-1][1] <= merge + 1e-9:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def _frames(meas: dict, n: int) -> list[float]:
    m = list(meas.get("m") or [])
    return (m + [SILENT] * n)[:n]


def speech_mask(dialog: list[float], level: float) -> list[bool]:
    """Кадры речи: громкость голоса+исходника не ниже уровня − SPEECH_BELOW_LEVEL_LU и не тише пола."""
    floor = max(SPEECH_FLOOR_LUFS, level - SPEECH_BELOW_LEVEL_LU)
    return [v >= floor for v in dialog]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return SILENT
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def _r(value: float, digits: int = 1) -> float | None:
    return None if value <= SILENT + 1 else round(value, digits)


def _spark(values: list[float], lo: float = -40.0, hi: float = -6.0) -> str:
    bars = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        if v <= SILENT + 1:
            out.append(" ")
            continue
        u = (min(hi, max(lo, v)) - lo) / (hi - lo)
        out.append(bars[min(len(bars) - 1, int(u * len(bars)))])
    return "".join(out)


def _timeline(short: list[float], duration: float) -> dict:
    step = 0.5 if duration <= 90 else 1.0
    every = max(1, int(round(step / FRAME_SEC)))
    points = [(round((i + 1) * FRAME_SEC, 1), v) for i, v in enumerate(short)][every - 1::every]
    values = [v for _t, v in points]
    return {"step_sec": step, "points": [{"t": t, "lufs": _r(v)} for t, v in points],
            "line": _spark(values), "line_scale": "▁ −40 … █ −6 LUFS (short-term, 3 с)"}


def analyze(model: dict, target: float = TARGET_LUFS, render_path: Path | None = None,
            final_measure: dict | None = None) -> dict:
    """Замеры стемов → цифры и вердикты (см. вверху)."""
    from app.render import LOUDNESS_PEAK_DB as LOUDNESS_PEAK
    from app.render import loudness_plan

    duration = float(model["duration"])
    n = max(1, int(round(duration / FRAME_SEC)))
    stems: dict[str, Path] = model["stems"]
    meas = {kind: ebur128(path) for kind, path in stems.items()}
    frames = {kind: _frames(m, n) for kind, m in meas.items()}
    silent_series = [SILENT] * n
    dialog_parts = [frames[k] for k in ("voice", "source") if k in frames]
    dialog = power_sum(*dialog_parts) if dialog_parts else silent_series
    dialog_level = p2db(sum(db2p(v) for v in dialog if v > SPEECH_FLOOR_LUFS) /
                        max(1, sum(1 for v in dialog if v > SPEECH_FLOOR_LUFS)))
    speechless_source = "voice" not in frames and "source" in frames and _source_speechless(model)
    mask = speech_mask(dialog, dialog_level) if dialog_parts and not speechless_source else [False] * n
    speech_frames = [i for i, on in enumerate(mask) if on]
    speech_sec = round(len(speech_frames) * FRAME_SEC, 1)
    speech_level = power_mean([dialog[i] for i in speech_frames]) if speech_frames else SILENT
    # «Ядро» речи — кадры, где голос в полную силу: на краях фраз окно 400 мс наполовину тишина, и
    # покадровое сравнение там врёт (музыка «громче» голоса на его же атаке).
    core_frames = [i for i in speech_frames if dialog[i] >= speech_level - SPEECH_CORE_LU]

    stems_out = {}
    for kind in STEMS:
        if kind not in meas:
            continue
        m = meas[kind]
        active = sum(1 for v in frames[kind] if v > SILENCE_LUFS)
        stems_out[kind] = {"label": STEM_LABELS[kind], "lufs": _r(m["i"]), "true_peak": _r(m["tp"]),
                           "active_sec": round(active * FRAME_SEC, 1),
                           "under_speech_lufs": _r(power_mean([frames[kind][i] for i in speech_frames]))
                           if speech_frames else None}

    # Сумма стемов — что придёт в финальный проход громкости.
    mix_path = Path(str(next(iter(stems.values())).parent / "mix.wav")) if stems else None
    mix_meas = ebur128(mix_wavs(list(stems.values()), mix_path, duration)) if mix_path else \
        {"m": [], "s": [], "i": SILENT, "tp": SILENT, "lra": 0.0}
    mix_frames = _frames(mix_meas, n)
    gain, limiter_db, aim = 0.0, 0.0, SILENT
    if mix_meas["i"] > SILENT + 1:
        # Тот же план, что у финального прохода (render.loudness_plan): линейная поправка, сверх потолка
        # +20 дБ — в пределах запаса пиков; что выше потолка пика — срежет лимитер (≤ 3 дБ без потерь).
        lp = loudness_plan({"input_i": mix_meas["i"], "input_tp": mix_meas["tp"]}, target)
        gain = float(lp["gain_db"])
        limiter_db = max(0.0, float(lp.get("overshoot_db") or 0.0))
        aim = float(lp.get("aim_lufs", mix_meas["i"] + gain))
    predicted = {"integrated": _r(aim),
                 "true_peak": _r(min(mix_meas["tp"] + gain, LOUDNESS_PEAK)) if mix_meas["tp"] > SILENT + 1 else None,
                 "final_gain_db": round(gain, 2), "limiter_db": round(limiter_db, 1),
                 "lra": round(mix_meas["lra"], 1)}
    final = None
    if final_measure is None and render_path is not None and Path(render_path).is_file():
        final_measure = ebur128(render_path)
    if final_measure is not None:
        final = {"integrated": _r(final_measure["i"]), "true_peak": _r(final_measure["tp"]),
                 "lra": round(final_measure["lra"], 1), "timeline": _timeline(final_measure["s"], duration)}

    out: dict = {"duration": round(duration, 2), "target": {"lufs": target, "true_peak": TRUE_PEAK_MAX_DB,
                                                           "music_under_voice_db": [MUSIC_UNDER_MIN_DB,
                                                                                   MUSIC_UNDER_TARGET_DB]},
                 "stems": stems_out, "predicted": predicted,
                 "speech": {"seconds": speech_sec, "level_lufs": _r(speech_level),
                            "spans": len(_ranges(mask)), "detected": "по громкости голоса и звука исходника"},
                 "timeline": final["timeline"] if final else _timeline(mix_meas["s"], duration)}
    if final:
        out["final"] = {k: v for k, v in final.items() if k != "timeline"}
    verdicts: list[dict] = []

    def verdict(severity: str, check: str, message: str, fix: str = "", suggest: dict | None = None, **extra: Any) -> None:
        item = {"severity": severity, "check": check, "message": message}
        if fix:
            item["fix"] = fix
        if suggest:
            item["suggest"] = suggest
        item.update(extra)
        verdicts.append(item)

    # ---- голос против музыки
    music_db_now = (model.get("music") or {}).get("volume_db")
    if speech_frames and "music" in frames:
        music_under = power_mean([frames["music"][i] for i in speech_frames])
        diff = speech_level - music_under
        per = [(i, dialog[i] - frames["music"][i]) for i in core_frames]
        worst = _percentile([d for _i, d in per], 0.1)
        bad_mask = [False] * n
        for i, d in per:
            if d < MUSIC_UNDER_MIN_DB:
                bad_mask[i] = True
        bad = _worst_ranges(bad_mask, per, n)
        vm = {"diff_db": _r(diff), "worst_db": _r(worst), "speech_lufs": _r(speech_level),
              "music_lufs": _r(music_under), "too_loud_at": bad}
        out["voice_vs_music"] = vm
        delta = diff - MUSIC_UNDER_TARGET_DB  # < 0 — музыку убавить на |delta|
        known = music_db_now is not None and music_db_now > SILENT + 1
        new_db = round(music_db_now + delta, 1) if known else None
        suggest = {"tool": "auto_mix", "args": {"music_under_voice_db": -MUSIC_UNDER_TARGET_DB}}
        manual = f"set_mix music_db={new_db:g} (сейчас {music_db_now:+.1f} дБ к файлу трека)" if known else ""
        if diff < MUSIC_UNDER_MIN_DB:
            now = (f"музыка ГРОМЧЕ голоса на {-diff:.1f} дБ" if diff < 0 else
                   f"голос выше музыки всего на {diff:.1f} дБ")
            verdict("warn", "music",
                    f"музыка громче нормы на {MUSIC_UNDER_TARGET_DB - diff:.0f} дБ под речью: {now} "
                    f"(норма — музыка на {MUSIC_UNDER_MIN_DB:g}–{MUSIC_UNDER_TARGET_DB:g} дБ тише речи)",
                    f"убавь музыку на {-delta:.0f} дБ под речью: auto_mix (сам выставит громкость и ducking)"
                    + (f" или {manual}" if manual else ""), suggest, diff_db=round(diff, 1),
                    music_db_suggested=new_db)
        elif diff < MUSIC_UNDER_GOOD_DB:
            verdict("info", "music",
                    f"музыка на грани: голос выше её на {diff:.1f} дБ (лучше {MUSIC_UNDER_GOOD_DB:g}–"
                    f"{MUSIC_UNDER_TARGET_DB:g})", f"убавь на {-delta:.0f} дБ — auto_mix" + (f" или {manual}" if manual else ""),
                    suggest, diff_db=round(diff, 1), music_db_suggested=new_db)
        elif diff > MUSIC_UNDER_MAX_DB:
            up = round(diff - MUSIC_UNDER_TARGET_DB, 1)
            verdict("info", "music", f"музыку под речью почти не слышно: на {diff:.0f} дБ тише голоса",
                    f"можно поднять на {up:g} дБ (auto_mix) — или так задумано",
                    {"tool": "auto_mix", "args": {}}, diff_db=round(diff, 1),
                    music_db_suggested=round(music_db_now + up, 1) if music_db_now is not None and music_db_now > SILENT + 1
                    else None)
        if bad and diff >= MUSIC_UNDER_MIN_DB:
            spots = ", ".join(f"{r['start']:.1f}–{r['end']:.1f} с" for r in bad[:3])
            verdict("info", "music", f"местами музыка всё же громкая под речью ({spots}): голос выше всего на "
                                     f"{min(r['diff_db'] for r in bad):.0f} дБ",
                    "set_music_duck duck на этих отрезках (−6…−10 дБ) или auto_mix", t=bad[0]["start"])
    elif "music" in frames and not speech_frames:
        out["voice_vs_music"] = {"diff_db": None, "note": "речи нет — музыка звучит одна"}

    # ---- звук видео-слоёв под речью
    if speech_frames and "video" in frames:
        under = power_mean([frames["video"][i] for i in speech_frames])
        diff = speech_level - under
        out["voice_vs_video"] = {"diff_db": _r(diff)}
        if diff < VIDEO_UNDER_MIN_DB and under > SILENCE_LUFS:
            verdict("warn", "video", f"звук видео-слоёв под речью громкий: голос выше всего на {diff:.1f} дБ",
                    f"убавь видео-слои на {VIDEO_UNDER_MIN_DB + 4 - diff:.0f} дБ: auto_mix или set_mix groups.video",
                    {"tool": "auto_mix", "args": {}}, diff_db=round(diff, 1))

    # ---- звуки против голоса
    item_rows = []
    if model.get("items"):
        for item in model["items"]:
            if item["role"] not in ("sfx", "video"):
                continue
            peak = item_peak_lufs(item)
            a, b = item["start"], item["end"]
            i0, i1 = max(0, _frame_of(a)), min(n, _frame_of(b) + 4)
            over_speech = [i for i in range(i0, i1) if mask[i]]
            ref = power_mean([dialog[i] for i in over_speech]) if over_speech else speech_level
            row = {"layer_id": item["layer_id"], "role": item["role"], "start": round(a, 2), "end": round(b, 2),
                   "peak_lufs": _r(peak), "over_speech": bool(over_speech),
                   "vs_voice_db": _r(peak - speech_level) if speech_level > SILENT + 1 and peak > SILENT + 1 else None}
            if item.get("mix_db"):
                row["mix_db"] = item["mix_db"]
            if speech_level > SILENT + 1 and peak > SILENT + 1:
                limit = (ref - SFX_MASK_DB) if over_speech else (speech_level + SFX_OVER_VOICE_DB)
                row["excess_db"] = round(peak - limit, 1)
            item_rows.append(row)
    if item_rows:
        out["layers"] = item_rows
    if speech_frames and "sfx" in frames:
        masking = [False] * n
        per = []
        for i in core_frames:
            d = dialog[i] - frames["sfx"][i]
            if frames["sfx"][i] > SILENCE_LUFS and d < SFX_MASK_DB:
                masking[i] = True
            per.append((i, d))
        ranges = _worst_ranges(masking, per, n)
        out["voice_vs_sfx"] = {"masking_at": ranges,
                               "max_sfx_lufs": _r(max(frames["sfx"])),
                               "speech_lufs": _r(speech_level)}
        for r in ranges[:3]:
            who = [row["layer_id"] for row in item_rows if row["start"] < r["end"] and row["end"] > r["start"]]
            need = min(SFX_MASK_DB - r["diff_db"], -LAYER_DB_RANGE[0] - 3)
            verdict("warn", "sfx",
                    f"на {r['start']:.1f}–{r['end']:.1f} с звук{'и' if len(who) > 1 else ''} "
                    f"{', '.join(who) or 'слоёв'} {'громче' if r['diff_db'] < 0 else 'почти как'} голос"
                    f"{'а' if r['diff_db'] < 0 else ''} ({-r['diff_db']:+.0f} дБ к речи) — слова тонут",
                    f"убавь на {need + 3:.0f} дБ (set_mix layer_gains {{{', '.join(repr(w) for w in who[:3])}: "
                    f"{-(need + 3):.0f}}}) или сдвинь звук в паузу; auto_mix сделает сам",
                    {"tool": "auto_mix", "args": {}}, t=r["start"], layers=who)
    loud_rows = [row for row in item_rows if (row.get("excess_db") or 0) > 0 and not row["over_speech"]]
    if loud_rows:
        worst_row = max(loud_rows, key=lambda r: r["excess_db"])
        verdict("warn", "sfx",
                f"звуки громче голоса: {len(loud_rows)} шт., громче всех {worst_row['layer_id']} на "
                f"{worst_row['start']:.1f} с (на {worst_row['vs_voice_db']:+.0f} дБ к речи)",
                f"убавь на {worst_row['excess_db'] + 2:.0f} дБ: auto_mix (выровняет каждый звук под голос) или "
                f"set_mix layer_gains", {"tool": "auto_mix", "args": {}}, t=worst_row["start"],
                layers=[r["layer_id"] for r in loud_rows][:10])

    # ---- итоговая громкость и пики
    final_i = final["integrated"] if final else predicted["integrated"]
    final_tp = final["true_peak"] if final else predicted["true_peak"]
    where = "готовый рендер" if final else "прогноз финального прохода"
    if final_i is not None and abs(final_i - target) > LOUDNESS_TOLERANCE_LU:
        verdict("warn" if final else "info", "loudness",
                f"громкость ролика {final_i:.1f} LUFS ({where}), цель {target:g} (±{LOUDNESS_TOLERANCE_LU:g})",
                "перерендерь (финальный проход выравнивает сам); тише цели при пике у потолка — пики мешают "
                "(убавь громкие звуки, auto_mix)" if final else "финальный проход поднимет/опустит сам",
                {"tool": "auto_mix", "args": {"target_lufs": target}})
    if final_tp is not None and final_tp > TRUE_PEAK_MAX_DB:
        verdict("warn", "peak", f"true peak {final_tp:.1f} dBTP ({where}) — выше {TRUE_PEAK_MAX_DB:g}: на площадках захрипит",
                "перерендерь; если повторяется — убавь самые громкие звуки (auto_mix)")
    if final and final_tp is not None and final_tp > -0.1:
        verdict("warn", "clipping", f"клиппинг: пик {final_tp:.1f} dBTP в готовом файле", "перерендерь, убавь звуки")
    if limiter_db > LIMITER_HEAVY_DB:
        verdict("warn" if limiter_db > 6 else "info", "peak",
                f"пики сведения на {limiter_db:.0f} дБ выше потолка — лимитер их срежет, громкость «качнёт»",
                "убавь самые громкие звуки/удары (auto_mix выровняет звуки под голос)", {"tool": "auto_mix", "args": {}})

    # ---- дыры тишины
    quiet = [v < SILENCE_LUFS for v in mix_frames]
    gaps = [(a, b) for a, b in _ranges(quiet, merge=0.05, shift=FRAME_SHIFT)
            if b - a >= SILENCE_MIN_SEC and a > 0.3 and b < duration - 0.3]
    # Стемы — по шкале clip ДО вырезов: тишина внутри выреза в ролик не попадёт (QA 02.11, клип 892:
    # «тишина 18.6–19.6 с» указывала прямо в вырез 18.47–19.66 с).
    gaps = silence_after_cuts(gaps, (model.get("rs") or {}).get("cuts") or [])
    if gaps:
        out["silence_gaps"] = [{"start": a, "end": b} for a, b in gaps[:MAX_RANGES]]
        verdict("info", "silence", "тишина в ролике: " + ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in gaps[:3]),
                "если не задумано — музыка/фон или подрежь паузу", t=gaps[0][0])
    if not dialog_parts:
        verdict("info", "speech", "голоса нет (ни слоёв-голоса, ни звука исходника) — соотношение голос/музыка не меряю")
    elif not speech_frames:
        verdict("info", "speech", "речь не найдена — соотношение голос/музыка не меряю")
    out["verdicts"] = verdicts
    out["summary"] = summary(out)
    return out


def silence_after_cuts(gaps: list[tuple[float, float]], cuts: list[dict],
                       min_len: float | None = None) -> list[tuple[float, float]]:
    """Дыры тишины (шкала clip до вырезов) без того, что вырезано: от дыры остаются куски вне вырезов;
    куски одной дыры по обе стороны выреза в ролике стоят встык — их длина складывается. Остаётся то, что
    в готовом ролике длиннее ``min_len`` (SILENCE_MIN_SEC); время — по шкале clip, от первого куска до
    последнего."""
    min_len = SILENCE_MIN_SEC if min_len is None else min_len
    spans = sorted((float(c["start"]), float(c["end"])) for c in cuts or [] if float(c["end"]) > float(c["start"]))
    if not spans:
        return gaps
    out: list[tuple[float, float]] = []
    for a, b in gaps:
        bits, cursor = [], a
        for c0, c1 in spans:
            if c1 <= cursor or c0 >= b:
                continue
            if c0 > cursor:
                bits.append((cursor, c0))
            cursor = max(cursor, c1)
        if cursor < b:
            bits.append((cursor, b))
        if bits and sum(y - x for x, y in bits) >= min_len - 1e-6:
            out.append((round(bits[0][0], 2), round(bits[-1][1], 2)))
    return out


def _source_speechless(model: dict) -> bool:
    """Звук исходника без речи (игра, музыка) — «голосом» его не считаем."""
    return bool(model.get("speechless"))


# Кадр i momentary-громкости — окно 400 мс, кончающееся на (i+1)·0.1 с: середина окна — i·0.1 − 0.1.
FRAME_SHIFT = -0.1


def _frame_of(t: float) -> int:
    return int(round((t - FRAME_SHIFT) / FRAME_SEC))


def _worst_ranges(mask: list[bool], per: list[tuple[int, float]], n: int) -> list[dict]:
    diffs = dict(per)
    out = []
    for a, b in _ranges(mask, merge=0.3, shift=FRAME_SHIFT):
        i0, i1 = _frame_of(a), _frame_of(b)
        values = [diffs[i] for i in range(i0, i1) if i in diffs and mask[i]]
        if values and b - a >= 0.2:
            out.append({"start": round(max(0.0, a), 2), "end": round(b, 2), "diff_db": round(min(values), 1)})
    out.sort(key=lambda r: r["diff_db"])
    return out[:MAX_RANGES]


def summary(report: dict) -> str:
    warns = [v for v in report.get("verdicts") or [] if v["severity"] == "warn"]
    parts = []
    vm = report.get("voice_vs_music") or {}
    if vm.get("diff_db") is not None:
        parts.append(f"голос над музыкой {vm['diff_db']:+.1f} дБ под речью")
    final = report.get("final") or report.get("predicted") or {}
    if final.get("integrated") is not None:
        parts.append(f"{final['integrated']:.1f} LUFS" + (" (рендер)" if report.get("final") else " (прогноз)"))
    if final.get("true_peak") is not None:
        parts.append(f"пик {final['true_peak']:.1f} dBTP")
    head = "сведение в норме" if not warns else f"замечаний {len(warns)}: " + "; ".join(w["message"] for w in warns[:2])
    return head + (" — " + ", ".join(parts) if parts else "")
