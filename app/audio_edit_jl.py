"""Звук клипа: J/L-склейки, ручки и услышанные субтитры (из ``app/audio_edit.py``)."""

from __future__ import annotations

import math
from typing import Any

from app.audio_edit_norm import MAX_SHIFT_SEC, _num, normalize_joins

JL_XFADE_SEC = 0.08
# J/L не забирает больше этой доли соседнего куска (иначе кусок «съеден» чужим звуком).
JL_MAX_SHARE = 0.45


# --------------------------------------------------------------------------- #
# J/L-склейки
# --------------------------------------------------------------------------- #


def part_shifts(parts: list[dict], segments: list[dict], joins: Any) -> list[float]:
    """Сдвиг звука на каждой склейке частей: + — L (звук A тянется под B), − — J (звук B раньше).

    Только на стыке разных кусков (последняя часть куска i → первая часть куска i+1); джамп-каты
    внутри куска — без J/L. ``lag`` куска A и ``lead`` куска B на одном стыке вычитаются."""
    from app.piece_speed import is_reversed

    cfg = {j["piece"]: j for j in normalize_joins(joins)}
    if not cfg or len(parts) < 2:
        return [0.0] * max(0, len(parts) - 1)
    ids = [s.get("id") for s in segments]
    out = []
    for k, (left, right) in enumerate(zip(parts, parts[1:])):
        li = left.get("piece", k) if "piece" in left else k
        ri = right.get("piece", k + 1) if "piece" in right else k + 1
        if li == ri or li >= len(ids) or ri >= len(ids) or is_reversed(left) or is_reversed(right):
            # Развёрнутый кусок: звук «за краем» исходника к нему не относится — без J/L.
            out.append(0.0)
            continue
        lag = float((cfg.get(ids[li]) or {}).get("lag") or 0.0)
        lead = float((cfg.get(ids[ri]) or {}).get("lead") or 0.0)
        out.append(round(lag - lead, 3))
    return out


def plan_jl(durations: list[float], overlaps: list[float], shifts: list[float], audio_mode: str = "smooth",
            jump_joins: list[bool] | None = None) -> dict:
    """План звука склеек с J/L: для каждой части ``head``/``tail`` (с: + — дочитать из исходника,
    − — отрезать), для каждой склейки ``xfade`` (0 — встык с де-кликом) и ``shift`` после
    ограничений. Сумма длин минус кроссфейды = длина картинки (как без J/L)."""
    n = len(durations)
    head = [0.0] * n
    tail = [0.0] * n
    xfade: list[float] = []
    used: list[float] = []
    for k in range(max(0, n - 1)):
        o = float(overlaps[k]) if k < len(overlaps) else 0.0
        s = float(shifts[k]) if k < len(shifts) else 0.0
        jump = bool(jump_joins) and k < len(jump_joins or []) and bool(jump_joins[k])
        room = JL_MAX_SHARE * min(durations[k], durations[k + 1])
        if jump:
            s = 0.0
        s = math.copysign(min(abs(s), MAX_SHIFT_SEC, room), s) if s else 0.0
        if abs(s) < 0.01:
            s = 0.0
        if s == 0.0:
            if o > 0 and audio_mode == "smooth":
                xfade.append(round(o, 3))
            elif o > 0:
                tail[k] -= o / 2
                head[k + 1] -= o / 2
                xfade.append(0.0)
            else:
                xfade.append(0.0)
        elif o > 0:
            tail[k] += s
            head[k + 1] -= s
            xfade.append(round(o, 3))
        else:
            pad = JL_XFADE_SEC / 2
            tail[k] += s + pad
            head[k + 1] += pad - s
            xfade.append(round(2 * pad, 3))
        used.append(round(s, 3))
    return {"head": [round(h, 3) for h in head], "tail": [round(t, 3) for t in tail],
            "xfade": xfade, "shift": used}


def handle_windows(parts: list[dict], plan: dict) -> list[dict]:
    """Какие «ручки» звука прочитать из исходника: ``[{part, side, start, length}]`` (секунды
    исходника; start < 0 — до начала записи, добьётся тишиной)."""
    out = []
    for k, part in enumerate(parts):
        head, tail = plan["head"][k], plan["tail"][k]
        if head > 1e-3:
            out.append({"part": k, "side": "head", "start": round(float(part["start_sec"]) - head, 3),
                        "length": round(head, 3)})
        if tail > 1e-3:
            out.append({"part": k, "side": "tail", "start": round(float(part["end_sec"]), 3),
                        "length": round(tail, 3)})
    return out


def jl_audio_filters(n: int, durations: list[float], plan: dict, handles: dict[tuple[int, str], int],
                     jump_joins: list[bool] | None, smooth: bool, out_label: str = "amain") -> list[str]:
    """Звук склейки с J/L: каждая часть ровно своей длины, ручки (входы ``handles[(k, side)]``)
    дописаны/лишнее отрезано, стыки — кроссфейдом ``xfade`` или встык с де-кликом."""
    from app.transitions import DECLICK_SEC, JUMP_DECLICK_SEC

    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    filters: list[str] = []
    lengths: list[float] = []
    for k in range(n):
        base = f"[{k}:a]{fmt},apad,atrim=end={durations[k]:.3f},asetpts=PTS-STARTPTS"
        head, tail = plan["head"][k], plan["tail"][k]
        if head < -1e-3:
            base += f",atrim=start={-head:.3f},asetpts=PTS-STARTPTS"
        length = durations[k] + min(0.0, head)
        if tail < -1e-3:
            base += f",atrim=end={length + tail:.3f},asetpts=PTS-STARTPTS"
            length += tail
        label = f"j{k}"
        filters.append(f"{base}[{label}b]")
        current = f"{label}b"
        if (k, "head") in handles:
            filters.append(f"[{handles[(k, 'head')]}:a]{fmt},apad,atrim=end={head:.3f},asetpts=PTS-STARTPTS[{label}h]")
            filters.append(f"[{label}h][{current}]concat=n=2:v=0:a=1[{label}c]")
            current = f"{label}c"
            length += head
        if (k, "tail") in handles:
            filters.append(f"[{handles[(k, 'tail')]}:a]{fmt},apad,atrim=end={tail:.3f},asetpts=PTS-STARTPTS[{label}t]")
            filters.append(f"[{current}][{label}t]concat=n=2:v=0:a=1[{label}e]")
            current = f"{label}e"
            length += tail
        filters.append(f"[{current}]anull[n{k}]")
        lengths.append(length)
    previous = "n0"
    acc = lengths[0] if lengths else 0.0
    for k in range(1, n):
        label = out_label if k == n - 1 else f"ja{k}"
        cf = plan["xfade"][k - 1]
        if cf > 0:
            filters.append(f"[{previous}][n{k}]acrossfade=d={cf:.3f}:c1=tri:c2=tri[{label}]")
            acc += lengths[k] - cf
        else:
            jump = bool(jump_joins) and k - 1 < len(jump_joins or []) and bool(jump_joins[k - 1])
            if smooth or jump:
                fade = JUMP_DECLICK_SEC if jump else DECLICK_SEC
                filters.append(f"[{previous}]afade=t=out:st={max(0.0, acc - fade):.3f}:d={fade}[{label}f]")
                filters.append(f"[n{k}]afade=t=in:st=0:d={fade}[{label}i]")
                filters.append(f"[{label}f][{label}i]concat=n=2:v=0:a=1[{label}]")
            else:
                filters.append(f"[{previous}][n{k}]concat=n=2:v=0:a=1[{label}]")
            acc += lengths[k]
        previous = label
    if n == 1:
        filters.append(f"[n0]anull[{out_label}]")
    return filters


def jl_speech_report(segments: list[dict], joins: Any, words: list[dict], transition: Any = None) -> list[dict]:
    """Слова, чей звук J/L заменяет звуком соседнего куска (их не будет слышно — и субтитры их
    не покажут, ``heard_subtitles``), по каждому стыку с J/L. ``words`` — слова субтитров
    «кусками подряд»."""
    from app.transitions import part_overlaps

    shifts = part_shifts(segments, segments, joins)
    if not any(shifts):
        return []
    lengths = [piece_length(s) for s in segments]
    plan = plan_jl(lengths, part_overlaps(segments, transition), shifts)
    out = []
    bound = 0.0
    for k, s in enumerate(plan["shift"]):
        bound += lengths[k]
        if not s:
            continue
        if s > 0:  # L: голова куска B не слышна
            lo, hi, who = bound, bound + s, "начало куска {}".format(k + 1)
        else:  # J: хвост куска A не слышен
            lo, hi, who = bound + s, bound, "конец куска {}".format(k)
        lost = [str(w.get("word") or "").strip() for w in words or []
                if _num(w.get("start")) is not None and _num(w.get("end")) is not None
                and float(w["start"]) < hi - 0.03 and float(w["end"]) > lo + 0.03]
        out.append({"join": k, "shift": s, "kind": "L" if s > 0 else "J", "hidden": who,
                    "hidden_sec": round(abs(s), 3), "lost_words": [w for w in lost if w][:8]})
    return out


# --------------------------------------------------------------------------- #
# Субтитры за звуком J/L
# --------------------------------------------------------------------------- #

# Сколько секунд исходника распознавать сверх ручки J/L (слово, начатое в ручке, — целиком).
HANDLE_ASR_MARGIN_SEC = 0.3
MAX_HANDLE_WORDS = 400


def piece_length(seg: dict) -> float:
    """Длина куска на шкале клипа, с (со скоростью куска — app/piece_speed.py)."""
    from app.piece_speed import seg_length

    return seg_length(seg)


def normalize_handle_words(raw: Any) -> list[dict]:
    """Слова звука «ручек» J/L: ``[{piece, word, start, end}]`` — ``piece`` id куска, время —
    секунды ИСХОДНИКА (не clip): кусок подрезали или переставили — слова ручки остаются верны."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        piece, a, b = _num(item.get("piece")), _num(item.get("start")), _num(item.get("end"))
        word = str(item.get("word") or "").strip()
        if piece is None or piece != int(piece) or a is None or b is None or not word:
            continue
        entry = {"piece": int(piece), "word": word[:80], "start": round(a, 3), "end": round(max(a, b), 3)}
        if item.get("speaker"):
            entry["speaker"] = str(item["speaker"])[:8]
        out.append(entry)
    out.sort(key=lambda w: (w["piece"], w["start"]))
    return out[:MAX_HANDLE_WORDS]


def handle_extents(segments: list[dict], joins: Any, transition: Any = None,
                   margin: float = HANDLE_ASR_MARGIN_SEC) -> list[tuple[float, float]]:
    """Сколько секунд исходника до начала (J) и после конца (L) каждого куска распознать вместе
    с ним, чтобы субтитры показали звук ручки: ``[(head, tail)]`` по кускам, 0 — без ручки."""
    from app.transitions import part_overlaps

    out = [[0.0, 0.0] for _ in segments]
    shifts = part_shifts(segments, segments, joins)
    if any(shifts):
        plan = plan_jl([piece_length(s) for s in segments], part_overlaps(segments, transition), shifts)
        for k, s in enumerate(plan["shift"]):
            if s > 0:
                out[k][1] = round(s + margin, 3)
            elif s < 0:
                out[k + 1][0] = round(-s + margin, 3)
    return [(a, b) for a, b in out]


def _jl_windows(parts: list[dict], segments: list[dict], joins: Any, overlaps: list[float],
                jump_joins: list[bool] | None) -> list[dict]:
    """Стыки с J/L на шкале «части подряд»: что скрыто и откуда взять слова ручки."""
    shifts = part_shifts(parts, segments, joins)
    if not any(shifts):
        return []
    lengths = [piece_length(p) for p in parts]
    plan = plan_jl(lengths, overlaps, shifts, "smooth", jump_joins)
    ids = [s.get("id") for s in segments]

    def seg_id(k: int) -> Any:
        index = int(parts[k]["piece"]) if "piece" in parts[k] else k
        return ids[index] if 0 <= index < len(ids) else None

    out = []
    bound = 0.0
    for k, s in enumerate(plan["shift"]):
        bound += lengths[k]
        if not s:
            continue
        o = float(overlaps[k]) if k < len(overlaps) else 0.0
        if s > 0:  # L: дальше звучит исходник после конца A — голова B не слышна
            end = float(parts[k]["end_sec"])
            cut = bound + s + o / 2
            out.append({"kind": "L", "piece": seg_id(k), "src": (end, end + s), "hidden": (bound, cut),
                        "keep_before": cut, "offset": bound + o - end})
        else:  # J: раньше звучит исходник перед началом B — хвост A не слышен
            start = float(parts[k + 1]["start_sec"])
            cut = bound + s - o / 2
            out.append({"kind": "J", "piece": seg_id(k + 1), "src": (start + s, start), "hidden": (cut, bound),
                        "keep_after": cut, "offset": bound - o - start})
    return out


def heard_subtitles(payload: dict, parts: list[dict], segments: list[dict], joins: Any,
                    overlaps: list[float] | None = None, jump_joins: list[bool] | None = None) -> dict:
    """Субтитры идут за ЗВУКОМ J/L (шкала «части подряд», до сдвига на переходы).

    J/L решает, чей звук слышен у стыка (картинка режется как раньше): у J (lead куска B) в
    последние ``lead`` секунд картинки A звучит исходник ПЕРЕД началом B, у L (lag куска A) в первые
    ``lag`` секунд B — исходник ПОСЛЕ конца A. Поэтому:

    * слова, чей звук заменён (хвост A у J, голова B у L), из субтитров убираются — их не слышно;
    * слова ручки (``payload["handle_words"]``, секунды исходника — их распознаёт
      ``generate_for_plan``) встают туда, где их слышно, тем же сдвигом, что у своего куска (звук
      куска синхронен с его картинкой): тайминги не растягиваются, только переносятся.

    Граница «чьё слово» — середина кроссфейда звука: у стыка без перехода — точка J/L, под
    переходом длиной ``o`` — на ``o/2`` дальше. Слова ручки сразу поправлены на overlap своего
    стыка: ``shift_for_transition`` сдвигает слово по моменту (чья часть), а слово ручки J лежит до
    стыка, хотя живёт по часам куска B (у L — наоборот)."""
    if not isinstance(payload, dict) or len(parts) < 2:
        return payload
    overlaps = list(overlaps or [])
    overlaps += [0.0] * (len(parts) - 1 - len(overlaps))
    windows = _jl_windows(parts, segments, joins, overlaps, jump_joins)
    if not windows:
        return payload
    handle = normalize_handle_words(payload.get("handle_words"))
    added: list[dict] = []
    for win in windows:
        lo, hi = win["src"]
        for w in handle:
            if w["piece"] != win["piece"] or not lo - 0.05 <= (w["start"] + w["end"]) / 2 <= hi + 0.05:
                continue
            a, b = max(w["start"], lo), min(w["end"], hi)
            if b - a < 0.02:
                continue
            start, end = round(a + win["offset"], 3), round(b + win["offset"], 3)
            mid = (start + end) / 2
            if ("keep_before" in win and mid >= win["keep_before"]) or ("keep_after" in win and mid < win["keep_after"]):
                continue
            word = {"word": w["word"], "start": max(0.0, start), "end": max(0.0, start, end), "jl": win["kind"]}
            if w.get("speaker"):
                word["speaker"] = w["speaker"]
            added.append(word)
    hidden = [win["hidden"] for win in windows]

    def heard(item: dict) -> bool:
        a, b = _num(item.get("start")), _num(item.get("end"))
        if a is None or b is None:
            return True
        mid = (a + b) / 2
        return not any(lo <= mid < hi for lo, hi in hidden)

    words = [w for w in payload.get("words") or [] if isinstance(w, dict) and heard(w)]
    lines = []
    for line in payload.get("lines") or []:
        a, b = (_num(line.get("start")), _num(line.get("end"))) if isinstance(line, dict) else (None, None)
        if a is None or b is None:
            continue
        inside = [w for w in words if a - 0.001 <= (float(w["start"]) + float(w["end"])) / 2 <= b + 0.001]
        if inside:
            lines.append({**line, "start": min(float(w["start"]) for w in inside),
                          "end": max(float(w["end"]) for w in inside),
                          "text": " ".join(str(w["word"]) for w in inside)})
        elif not payload.get("words") and heard(line):
            lines.append(line)
    for run in _runs(added):
        lines.append({"start": run[0]["start"], "end": run[-1]["end"], "text": " ".join(w["word"] for w in run)})
    return {**payload, "words": sorted(words + added, key=lambda w: float(w["start"])),
            "lines": sorted(lines, key=lambda line: float(line["start"]))}


def _runs(words: list[dict], gap: float = 0.6) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: w["start"]):
        if out and w["start"] - out[-1][-1]["end"] <= gap:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def split_handle_words(payload: dict, ext_pieces: list[tuple[float, float]], segments: list[dict]) -> dict:
    """Распознавание шло по кускам С ручками J/L (``ext_pieces`` — секунды исходника, кусок плюс
    ручки): слова внутри кусков → шкала «куски подряд» (как всегда), слова ручек →
    ``handle_words`` (секунды исходника, id куска). Строки — по словам внутри кусков."""
    layout, off = [], 0.0
    for a, b in ext_pieces:
        layout.append((float(a), float(b), off))
        off += float(b) - float(a)
    plain_starts, acc = [], 0.0
    for seg in segments:
        plain_starts.append(acc)
        acc += max(0.0, float(seg["end_sec"]) - float(seg["start_sec"]))  # распознано без скорости

    def classify(item: dict) -> tuple[bool, dict] | None:
        a, b = _num(item.get("start")), _num(item.get("end"))
        if a is None or b is None or not layout:
            return None
        mid = (a + b) / 2
        k = next((i for i, (x, y, o) in enumerate(layout) if mid < o + (y - x)), len(layout) - 1)
        e0, _e1, o = layout[k]
        sa, sb = e0 + a - o, e0 + b - o
        s0, s1 = float(segments[k]["start_sec"]), float(segments[k]["end_sec"])
        if not s0 <= (sa + sb) / 2 < s1:
            return False, {**item, "piece": segments[k].get("id"), "start": round(sa, 3), "end": round(sb, 3)}
        p = plain_starts[k]
        return True, {**item, "start": round(p + min(max(sa, s0), s1) - s0, 3),
                      "end": round(p + min(max(sb, s0), s1) - s0, 3)}

    words, handle = [], []
    for w in payload.get("words") or []:
        got = classify(w) if isinstance(w, dict) else None
        if got:
            (words if got[0] else handle).append(got[1])
    lines = []
    for line in payload.get("lines") or []:
        got = classify(line) if isinstance(line, dict) else None
        if not got or not got[0]:
            continue
        line = got[1]
        inside = [w for w in words if line["start"] - 0.001 <= (w["start"] + w["end"]) / 2 <= line["end"] + 0.001]
        if inside:  # строка задела ручку — по своим словам внутри куска
            line = {**line, "start": inside[0]["start"], "end": inside[-1]["end"],
                    "text": " ".join(str(w["word"]) for w in inside)}
        lines.append(line)
    # Какие окна исходника распознаны (и пустые — тишина в ручке тоже ответ).
    spans = []
    for (a, b), seg in zip(ext_pieces, segments):
        s0, s1 = float(seg["start_sec"]), float(seg["end_sec"])
        if a < s0 - 1e-3:
            spans.append({"piece": seg.get("id"), "side": "head", "start": round(a, 3), "end": round(s0, 3)})
        if b > s1 + 1e-3:
            spans.append({"piece": seg.get("id"), "side": "tail", "start": round(s1, 3), "end": round(b, 3)})
    return {**payload, "words": words, "lines": lines, "handle_words": normalize_handle_words(handle),
            "handle_spans": spans, "duration": round(acc, 3)}
