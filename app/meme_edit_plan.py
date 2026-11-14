"""Мем-эдит: раскладка ролика по долям музыки (чистые функции — их проверяют тесты).

Из боёв (app/meme_edit_fights.py, по порядку ролика) и мемов (app/meme_edit_memes.py) собираются **блоки** — по
одному на бой; блок — отдельный клип сервиса (куски + вставки мемов), потом блоки склеиваются с одной музыкальной
подложкой (app/meme_edit_concat.py). Что одобрил владелец (пилот 2, v4):

* **всё в долю:** готовый ролик идёт по сетке долей трека ``t = k·period`` от 0 (музыка стартует с сильной доли);
  конец каждого куска сдвигается на ближайшую долю (≤ полдоли раньше или позже), но не режет слово и начало
  стрельбы — тогда позже (до 4 долей). Начало куска не двигается никогда (начало боя остаётся). Слот мема — целое
  число долей, поэтому и возврат в игру в долю;
* **мем сразу после сетапа:** кусок режется в конце реплики, мем закрывает кадр, под ним — невидимый «наполнитель»
  (продолжение записи, звук игры и микрофона там глушится), после мема бой продолжается с того же места;
* **переходы** на каждом стыке блоков и на каждом вырезе мёртвого времени: зум-панч, глитч, вжух, RGB-сдвиг, тряска —
  по кругу, два одинаковых подряд не бывает; на стыке актов — белая вспышка;
* **музыка короче ролика** — повтор с начала рабочей части на границе такта; шов — на стыке блоков (там переход),
  поэтому блок перед швом кончается ровно на такт.

Шкалы: source (исходник), clip (блок: куски подряд, вырезов и наплывов нет), final (весь ролик = начало блока + clip).
"""

from __future__ import annotations

import math
from typing import Any

from app.clip_spec import MAX_PIECES  # noqa: F401 - один предел кусков на весь сервис (клип-блок)
from app.meme_edit_fights import Fight
from app.meme_edit_memes import Pick

FPS = 30.0
MIN_PIECE = 1.5          # минимум куска сервиса
SPLIT_MIN = 1.9          # кусок у мема короче — расширяется в вырезанное время
MAX_NUDGE_BEATS = 4      # конец куска позже не больше чем на столько долей
TRANSITIONS = ("zoompunch", "glitch", "whip", "rgb_split", "shake")
ACT_EVERY = 3            # горизонталь: акт — столько блоков, на стыке актов — вспышка
NO_TEXT_BEFORE_SEC = 2.5
WORD_EVERY_SEC = 80.0    # жёлтое слово-цитата не чаще
SEAM_MARGIN_SEC = 6.0


def frame(t: float) -> float:
    return round(round(float(t) * FPS) / FPS, 4)


def meme_beats(length: float, period: float, min_sec: float = 0.0) -> int:
    """Слот мема в долях: не короче удара (хвост ≤ 0.12 с можно срезать — там тишина) и не короче ``min_sec``."""
    n = max(2, math.ceil(max(0.0, length - 0.12) / period - 1e-6))
    while n * period < min_sec - 1e-6:
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Куски боя с местами под мемы
# --------------------------------------------------------------------------- #


def split_at_memes(pieces: list[list[float]], picks: list[Pick], duration: float) -> list[dict]:
    """Куски боя ``[{a, b, pick}]``: кусок режется в точке мема (``pick`` — мем после куска). Точка мема в вырезанном
    мёртвом времени — мем в конце предыдущего куска; после конца боя — в конце боя. Кусок у мема короче SPLIT_MIN
    расширяется в вырезанное время (сначала вперёд), пересекшиеся куски склеиваются."""
    segs = [{"a": float(a), "b": float(b), "pick": None} for a, b in pieces]
    for pick in sorted(picks, key=lambda p: p.cand.t):
        m = float(pick.cand.t)
        idx = next((i for i, s in enumerate(segs) if s["a"] - 0.05 <= m <= s["b"] + 0.05), None)
        if idx is None:
            before = [i for i, s in enumerate(segs) if s["b"] <= m]
            idx = before[-1] if before else 0
            m = segs[idx]["b"]
        s = segs[idx]
        m = min(max(m, s["a"]), s["b"])
        if s["b"] - m < 0.05:
            if s["pick"] is None:
                s["pick"] = pick
            continue
        if m - s["a"] < 0.05:
            if idx > 0 and segs[idx - 1]["pick"] is None:
                segs[idx - 1]["pick"] = pick
            continue
        segs[idx:idx + 1] = [{"a": s["a"], "b": m, "pick": pick}, {"a": m, "b": s["b"], "pick": s["pick"]}]
    # короткие куски — расширить в вырезанное время
    i = 0
    while i < len(segs):
        s = segs[i]
        if s["b"] - s["a"] >= SPLIT_MIN:
            i += 1
            continue
        if s["pick"] is None or i == len(segs) - 1:
            want = s["a"] + SPLIT_MIN
            if want <= duration - 0.05:
                s["b"] = max(s["b"], want)
            else:
                s["a"] = max(0.0, min(s["a"], s["b"] - SPLIT_MIN))
        else:
            s["a"] = max(0.0, s["b"] - SPLIT_MIN)
        # пересёкся с соседом — склейка
        if i + 1 < len(segs) and segs[i + 1]["a"] < s["b"] - 1e-6 and s["pick"] is None:
            nxt = segs.pop(i + 1)
            s["b"], s["pick"] = max(s["b"], nxt["b"]), nxt["pick"]
            continue
        if i > 0 and segs[i - 1]["b"] > s["a"] + 1e-6 and segs[i - 1]["pick"] is None:
            prev = segs.pop(i - 1)
            s["a"] = min(prev["a"], s["a"])
            i -= 1
            continue
        i += 1
    return segs


# --------------------------------------------------------------------------- #
# Доли
# --------------------------------------------------------------------------- #


def _cuts_word(nz: float, b: float, words: list[dict], shots: list[list[float]]) -> bool:
    """Конец куска ``nz`` (вместо ``b``) режет слово, срезает начало слова или начало стрельбы?"""
    for w in words:
        ws, we = float(w["start"]), float(w["end"])
        if ws < nz < we - 0.03:
            return True
        if nz < b and nz - 0.02 <= ws < b:
            return True
    if nz < b and any(nz - 0.3 <= float(s[0]) < b for s in shots):
        return True
    return False


def snap_end(a: float, b: float, t_out: float, period: float, *, words: list[dict], shots: list[list[float]],
             duration: float, bar_after: int | None = None, limit: float | None = None) -> tuple[float, int]:
    """Конец куска ``[a, b]``, идущего в ролике с ``t_out``, на долю. Ответ — (новый конец в source, номер доли).

    Ближайшая доля, если сдвиг ≤ полдоли и он не режет слово/стрельбу; иначе позже (до MAX_NUDGE_BEATS долей).
    ``bar_after`` — после куска ещё столько долей (мем), и всё вместе должно кончиться на такт (шов музыки).
    ``limit`` — дальше конец не уходит (там начинается следующий кусок: кадры не повторяются)."""
    hi = min(duration - 0.05, limit if limit is not None else duration)
    end_out = t_out + (b - a)
    k = round(end_out / period)
    cand = list(range(k - (1 if k * period > end_out else 0), k + MAX_NUDGE_BEATS + 1))
    cand.sort(key=lambda c: (abs(c * period - end_out) > period / 2 + 1e-6, c * period < end_out - 1e-6,
                             abs(c * period - end_out)))
    if bar_after is not None:
        span = range(k - 4, k + 5)
        cand = sorted((c for c in span if (c + bar_after) % 4 == 0),
                      key=lambda c: (c * period < end_out - 1e-6, abs(c * period - end_out)))

    def nz_of(c: int) -> float:
        return round(a + frame(c * period) - frame(t_out), 4)

    for c in cand:
        nz = nz_of(c)
        if nz - a < MIN_PIECE - 1e-6 or nz > hi + 1e-6:
            continue
        if not _cuts_word(nz, b, words, shots):
            return nz, c
    for c in cand:  # всё режет слово — берём ближайшую позднюю, что влезает в запись
        nz = nz_of(c)
        if nz - a >= MIN_PIECE - 1e-6 and nz <= hi + 1e-6:
            return nz, c
    c = max(k, math.ceil((t_out + MIN_PIECE) / period))
    while c > 0 and nz_of(c) > hi + 1e-6 and nz_of(c - 1) - a >= MIN_PIECE - 1e-6:
        c -= 1
    return nz_of(c), c


def _words_in(words: list[dict], a: float, z: float) -> list[dict]:
    return [w for w in words if float(w["start"]) >= a - 1e-3 and float(w["end"]) <= z + 0.06]


def lay_block(fight: Fight, picks: list[Pick], punch_len: dict[int, float], *, t0: float, period: float,
              duration: float, owner_words: list[dict], team_words: list[dict], shots: list[list[float]],
              seam_after: bool = False) -> dict:
    """Один бой → блок: куски клипа (с наплывом-наполнителем под мемами), вставки мемов, слова в шкале clip, все
    стыки на долях. ``t0`` — начало блока в ролике (на доле)."""
    words = sorted(owner_words + team_words, key=lambda w: float(w["start"]))
    segs = split_at_memes(fight.pieces, picks, duration)
    t = frame(t0)
    k_beat = round(t0 / period)
    pieces: list[dict] = []
    visible: list[dict] = []
    inserts: list[dict] = []
    prev_pick = False
    for i, s in enumerate(segs):
        pick: Pick | None = s["pick"]
        last = i == len(segs) - 1
        if prev_pick and visible and abs(s["a"] - visible[-1]["z"]) <= 0.8:
            s["a"] = visible[-1]["z"]  # после мема бой идёт ровно с того кадра, где его закрыл мем
        limit = None
        if pick is None and not last and segs[i + 1]["a"] > s["b"] + 1e-6:
            limit = segs[i + 1]["a"]
        nb = 0
        if pick is not None:
            # наполнитель встык нельзя (конец записи) — слот не короче минимума куска
            room = duration - 0.05 - s["b"]
            need = punch_len.get(int(pick.meme["meme_id"]), 1.8)
            nb = meme_beats(need, period, 0.0 if room >= need + period * 2 else MIN_PIECE)
        z, k_end = snap_end(s["a"], s["b"], t, period, words=words, shots=shots, duration=duration,
                            bar_after=(nb if (seam_after and last) else None), limit=limit)
        prev_pick = pick is not None
        clip_in = round(t - frame(t0), 4)
        visible.append({"a": round(s["a"], 4), "z": z, "clip_in": clip_in})
        t = frame(k_end * period)
        piece = {"start_sec": round(s["a"], 4), "end_sec": z, "title": f"{fight.key}: бой"}
        if pick is not None:
            slot = round(frame((k_end + nb) * period) - t, 4)
            at = round(t - frame(t0), 4)
            if z + slot <= duration - 0.05:
                piece["end_sec"] = round(z + slot, 4)  # наполнитель встык: один кусок (под мемом не видно)
                pieces.append(piece)
            else:
                pieces.append(piece)
                fa = round(max(0.0, z - slot - 0.1), 4)
                pieces.append({"start_sec": fa, "end_sec": round(fa + slot, 4), "title": f"{fight.key}: под мемом"})
            line_start = pick.cand.line_start
            inserts.append({"meme_id": int(pick.meme["meme_id"]), "asset_id": int(pick.meme["asset_id"]),
                            "title": pick.meme.get("title") or "", "at": at, "slot": slot, "kind": pick.cand.kind,
                            "after_line": pick.cand.text[:120], "echo": pick.echo, "fit": pick.fit,
                            "line_start": None if line_start is None else _to_clip(visible, line_start)})
            t = frame(t + slot)
        else:
            pieces.append(piece)
    total = round(t - frame(t0), 4)
    clip_words = []
    for v in visible:
        for w in _words_in(owner_words, v["a"], v["z"]):
            clip_words.append(_clip_word(w, v, "A"))
        for w in _words_in(team_words, v["a"], v["z"]):
            clip_words.append(_clip_word(w, v, "B"))
    clip_words.sort(key=lambda w: (w["start"], w["speaker"]))
    return {"key": fight.key, "source_id": fight.source_id, "fight": fight.brief(), "pieces": pieces,
            "visible": visible, "inserts": inserts, "words": clip_words, "total": total,
            "final_at": round(frame(t0), 4), "seam_after": bool(seam_after), "k_start": k_beat,
            "events": [{"t": _to_clip(visible, float(e["t"])), "kind": e["kind"]} for e in fight.events
                       if _to_clip(visible, float(e["t"])) is not None]}


def _clip_word(w: dict, v: dict, speaker: str) -> dict:
    a = round(v["clip_in"] + float(w["start"]) - v["a"], 3)
    b = round(v["clip_in"] + float(w["end"]) - v["a"], 3)
    return {"word": str(w["word"]).strip(), "start": a, "end": max(b, round(a + 0.06, 3)), "speaker": speaker}


def _to_clip(visible: list[dict], t: float) -> float | None:
    for v in visible:
        if v["a"] - 1e-3 <= t <= v["z"] + 1e-3:
            return round(v["clip_in"] + t - v["a"], 3)
    return None


# --------------------------------------------------------------------------- #
# Весь ролик
# --------------------------------------------------------------------------- #


def estimate(fight: Fight, picks: list[Pick], punch_len: dict[int, float], period: float) -> float:
    return fight.length + sum(meme_beats(punch_len.get(int(p.meme["meme_id"]), 1.8), period) * period for p in picks)


def plan_seams(lengths: list[float], usable_sec: float | None) -> list[int]:
    """Где шов музыки: номера блоков, после которых трек начинается снова (рабочая часть короче ролика)."""
    if not usable_sec or sum(lengths) <= usable_sec - 1.0:
        return []
    seams, seg_start, acc = [], 0.0, 0.0
    for i, length in enumerate(lengths[:-1]):
        acc += length
        if acc + lengths[i + 1] - seg_start > usable_sec - SEAM_MARGIN_SEC:
            seams.append(i)
            seg_start = acc
    return seams


def transition_kinds(count: int, flash: set[int]) -> list[str]:
    """Виды переходов по кругу TRANSITIONS, на местах ``flash`` — вспышка; два одинаковых подряд — никогда."""
    out: list[str] = []
    k = 0
    for i in range(count):
        if i in flash and (not out or out[-1] != "flash"):
            out.append("flash")
            continue
        kind = TRANSITIONS[k % len(TRANSITIONS)]
        if out and kind == out[-1]:
            k += 1
            kind = TRANSITIONS[k % len(TRANSITIONS)]
        out.append(kind)
        k += 1
    return out


def plan_reel(fights: list[Fight], picks: list[Pick], punch_len: dict[int, float], *, period: float,
              usable_sec: float | None, durations: dict[int, float], words: dict[int, tuple[list, list]],
              shots: dict[int, list], vertical: bool = False) -> dict:
    """Блоки ролика по порядку ``fights``: доли, мемы, слова, переходы, эффекты, швы музыки."""
    by_fight: dict[str, list[Pick]] = {}
    for p in picks:
        by_fight.setdefault(p.cand.fight.key, []).append(p)
    lengths = [estimate(f, by_fight.get(f.key, []), punch_len, period) for f in fights]
    seams = set(plan_seams(lengths, usable_sec))
    blocks: list[dict] = []
    t = 0.0
    for i, f in enumerate(fights):
        owner, team = words.get(f.source_id, ([], []))
        block = lay_block(f, by_fight.get(f.key, []), punch_len, t0=t, period=period,
                          duration=durations[f.source_id], owner_words=owner, team_words=team,
                          shots=shots.get(f.source_id, []), seam_after=i in seams)
        blocks.append(block)
        t = block["final_at"] + block["total"]
    total = round(t, 4)
    # переходы: стык блоков (в начале входящего блока) и вырезы мёртвого времени внутри блока
    spots: list[tuple[int, float, str]] = []
    for bi, b in enumerate(blocks):
        if bi > 0:
            spots.append((bi, 0.0, "join"))
        after_meme = {round(ins["at"] + ins["slot"], 3) for ins in b["inserts"]}
        for j in range(1, len(b["visible"])):
            v0, v1 = b["visible"][j - 1], b["visible"][j]
            if round(v1["clip_in"], 3) in after_meme or abs(v1["a"] - v0["z"]) <= 0.05:
                continue
            spots.append((bi, v1["clip_in"], "cut"))
    flash = {n for n, (bi, _t, where) in enumerate(spots)
             if where == "join" and (vertical or bi % ACT_EVERY == 0 or blocks[bi - 1]["seam_after"])}
    kinds = transition_kinds(len(spots), flash)
    for b in blocks:
        b["transitions"] = []
    for (bi, at, where), kind in zip(spots, kinds):
        b = blocks[bi]
        near = any(w["start"] - 0.35 < at < w["end"] + 0.1 for w in b["words"])
        b["transitions"].append({"t": round(at, 4), "where": where, "kind": kind, "speech_near": near,
                                 "final": round(b["final_at"] + at, 3)})
    for b in blocks:
        b["flash_in"] = any(tr["kind"] == "flash" and tr["where"] == "join" for tr in b["transitions"])
        b["fx"] = block_fx(b)
    words_layers = yellow_words(blocks, total)
    for b in blocks:
        b["yellow"] = words_layers.get(b["key"], [])
    seam_times = [round(blocks[i]["final_at"] + blocks[i]["total"], 4) for i in sorted(seams)]
    return {"blocks": blocks, "total": total, "period": period, "seams": seam_times,
            "memes": [p.brief() for p in picks]}


def block_fx(block: dict) -> list[dict]:
    """Лёгкий зум 1.15× на плашки НОКАУТ / УНИЧТОЖЕНИЕ ОТРЯДА (не ближе 2.5 с друг к другу), на «ВЫ ЧЕМПИОН» —
    синий негатив и удар басом. Не на кадрах, закрытых мемом."""
    out: list[dict] = []
    last = -10.0
    covered = [(ins["at"], ins["at"] + ins["slot"]) for ins in block["inserts"]]
    for e in block["events"]:
        t = float(e["t"])
        if t is None or any(a - 0.4 <= t <= b for a, b in covered) or t > block["total"] - 0.5:
            continue
        if e["kind"] == "win":
            out += [{"type": "negate", "t": t}, {"type": "bass", "t": t}, {"type": "zoom", "t": t, "zoom": 1.15}]
            last = t
        elif e["kind"] in ("knock", "squad_wipe") and t - last >= 2.5:
            out.append({"type": "zoom", "t": t, "zoom": 1.2 if e["kind"] == "squad_wipe" else 1.15})
            last = t
    return out


def yellow_words(blocks: list[dict], total: float) -> dict[str, list[dict]]:
    """Жёлтые слова — цитата игрока перед самыми точными мемами (эхо или лучший смысл): 1–3 последних слова реплики,
    не чаще WORD_EVERY_SEC, не в первые 2.5 с ролика; только где реплика видна целиком."""
    budget = max(1, int(total // WORD_EVERY_SEC))
    cands = []
    for b in blocks:
        for ins in b["inserts"]:
            if not ins["after_line"] or ins["line_start"] is None:
                continue
            cands.append((len(ins["echo"]) * 10 + ins["fit"], b, ins))
    cands.sort(key=lambda c: -c[0])
    out: dict[str, list[dict]] = {}
    used: list[float] = []
    for _score, b, ins in cands:
        if len(used) >= budget:
            break
        start = float(ins["line_start"])
        final = b["final_at"] + start
        if final < NO_TEXT_BEFORE_SEC or any(abs(final - u) < WORD_EVERY_SEC / 2 for u in used):
            continue
        tail = [w for w in ins["after_line"].split() if w.strip(",.!?…")][-3:]
        text = " ".join(tail).strip(",. ").upper()
        while len(text) > 18 and len(tail) > 1:
            tail = tail[1:]
            text = " ".join(tail).strip(",. ").upper()
        if not text:
            continue
        end = ins["at"] - 0.02
        if end - start < 0.5:
            start = max(0.0, end - 0.8)
        out.setdefault(b["key"], []).append({"text": text, "start": round(start, 3), "end": round(end, 3)})
        used.append(final)
    return out


def music_segments(total: float, seams: list[float], song_start: float, period: float,
                   usable_sec: float | None = None) -> list[dict]:
    """Подложка: ``[{t, pos, len}]`` — с ``t`` ролика играет трек с ``pos`` (сильная доля рабочей части). Первый
    отрезок — с начала рабочей части; следующие — с фразы на 8 или 16 тактов дальше (если влезают до затухания),
    чтобы повтор не звучал одинаково. ``pos`` — всегда начало такта."""
    bar = 4 * period
    cuts = [0.0, *[s for s in seams if 0 < s < total], total]
    out = []
    for j, (a, b) in enumerate(zip(cuts, cuts[1:])):
        pos = song_start
        if j and usable_sec:
            room = max(0.0, usable_sec - (b - a))
            pos = song_start + min(8 * bar * (1 + (j - 1) % 2), math.floor(room / bar + 1e-9) * bar)
        out.append({"t": round(a, 4), "pos": round(pos, 4), "len": round(b - a, 4)})
    return out


def summary(plan: dict) -> dict[str, Any]:
    return {"total": plan["total"], "period": plan["period"], "seams": plan["seams"],
            "blocks": [{"key": b["key"], "final_at": b["final_at"], "total": b["total"],
                        "pieces": [[p["start_sec"], p["end_sec"]] for p in b["pieces"]],
                        "memes": [{"at": i["at"], "slot": i["slot"], "meme_id": i["meme_id"], "title": i["title"],
                                   "after": i["after_line"][:60]} for i in b["inserts"]],
                        "transitions": [(tr["kind"], tr["final"]) for tr in b["transitions"]],
                        "yellow": [y["text"] for y in b.get("yellow", [])]} for b in plan["blocks"]],
            "memes": plan["memes"]}
