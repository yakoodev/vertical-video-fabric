"""Части длинного ролика для TikTok: ≤ 58 с песни в каждой, стыки — на долях и не посреди боя.

Зачем (журнал монтажа 15.11, плейбук frag-movie, шаг 8): TikTok глушит ролик целиком, если в нём больше 60 с
одной песни со стримингов (личный аккаунт, проверено: 54 с BURN играют). Длинный мувик для TikTok — серия частей:
в каждой один непрерывный кусок песни ≤ 58 с, бои целиком, плашка «часть N/M», в конце «Часть N+1 → в профиле».

Здесь — только план (чистая функция, без медиа): ``plan_parts`` получает длину готового ролика, где в нём звучит
песня, сетку долей (доли, сильные доли; фраза — ``phrase_every`` тактов) и отрезки боёв/событий и возвращает части.

Как выбираются стыки. Кандидаты — доли сетки вне боёв: начало фразы (лучше всего), сильная доля, просто доля;
доля прямо перед началом боя (≤ ``HOOK_LEAD_SEC``) — бонус: часть начинается с боя, крючок в первые секунды. Нет
доли в просвете между боями — середина просвета (хуже всего, с пометкой). Внутри боя стыка нет никогда; бой длиннее
``max_song_sec`` песни — ошибка с объяснением. Точки, где песня начинается заново (повтор трека), — обязательные
стыки: в части один непрерывный кусок песни.

Сначала — наименьшее число частей, при котором это возможно (или ``parts``, если задано больше), потом — по
динамическому программированию — стыки: части близкой длины и стыки получше (фраза > сильная доля > доля).
"""

from __future__ import annotations

import bisect
import math
from typing import Any, Iterable

MAX_SONG_SEC = 58.0  # TikTok глушит > 60 с одной песни; запас 2 с
MIN_PART_SEC = 15.0
PHRASE_EVERY = 4  # тактов во фразе (16 долей при 4/4)
HOOK_LEAD_SEC = 1.5
LABEL_SEC = 2.5
END_TEXT_SEC = 2.0
BALANCE_WEIGHT = 10.0  # штраф за отклонение длины части от средней: 10·(Δ/средняя)²
EQUAL_WEIGHT = 400.0  # «части одной длины»: 5 % отклонения стоят дороже, чем доля вместо начала фразы
COSTS = {"phrase": 0.0, "downbeat": 0.6, "beat": 1.5, "song_edge": 0.3, "gap": 4.0}
HOOK_BONUS = 0.4
LABEL_TEXT = "часть {n}/{m}"
NEXT_TEXT = "Часть {next} → в профиле"
EPS = 1e-6


def _spans(raw: Iterable[Any] | None) -> list[tuple[float, float]]:
    """События ``[{start, end}]`` или ``[(start, end)]`` → отсортированные слитые отрезки."""
    items: list[tuple[float, float]] = []
    for item in raw or []:
        if isinstance(item, dict):
            a, b = item.get("start"), item.get("end")
        else:
            a, b = item[0], item[1]
        a, b = float(a), float(b)
        if b > a:
            items.append((a, b))
    merged: list[list[float]] = []
    for a, b in sorted(items):
        if merged and a < merged[-1][1] - EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _inside(t: float, spans: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Отрезок, внутри которого (строго) стоит ``t``, или None. Край боя — можно."""
    for a, b in spans:
        if a + EPS < t < b - EPS:
            return a, b
    return None


def phrase_marks(beats: list[float], downbeats: list[float] | None, phrase_every: int = PHRASE_EVERY) -> dict[float, str]:
    """Доли → вид: ``phrase`` (каждая ``phrase_every``-я сильная доля, от первой), ``downbeat``, ``beat``.
    Сильных долей нет — каждая четвёртая доля от первой."""
    beats = sorted({round(float(t), 4) for t in beats})
    downs = sorted({round(float(t), 4) for t in downbeats}) if downbeats else beats[::4]
    kinds = {t: "beat" for t in beats}
    for k, t in enumerate(downs):
        kinds[t] = "phrase" if k % max(1, int(phrase_every)) == 0 else "downbeat"
    return kinds


def boundary_candidates(duration: float, *, beats: list[float], downbeats: list[float] | None = None,
                        phrase_every: int = PHRASE_EVERY, events: Any = None,
                        song: tuple[float, float] | None = None, song_breaks: Iterable[float] = ()) -> list[dict]:
    """Кандидаты в стыки ``[{t, kind, cost}]`` строго внутри (0, duration), не внутри боёв."""
    spans = _spans(events)
    starts = [a for a, _b in spans]
    out: dict[float, dict] = {}

    def add(t: float, kind: str) -> None:
        t = round(float(t), 3)
        if not EPS < t < duration - EPS or _inside(t, spans):
            return
        cost = COSTS[kind]
        i = bisect.bisect_left(starts, t - EPS)
        if i < len(starts) and 0 <= starts[i] - t <= HOOK_LEAD_SEC:
            cost -= HOOK_BONUS  # часть начнётся с боя
        if t not in out or out[t]["cost"] > cost:
            out[t] = {"t": t, "kind": kind, "cost": round(cost, 3)}

    for t, kind in phrase_marks(beats, downbeats, phrase_every).items():
        add(t, kind)
    for t in [*(song or ()), *song_breaks]:
        add(t, "song_edge")
    # Просвет между боями без единой доли — середина просвета (худший стык, но не посреди боя).
    edges = [0.0, *[x for span in spans for x in span], float(duration)]
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a > 2 * EPS and not any(a - EPS <= c <= b + EPS for c in out):
            add((a + b) / 2, "gap")
    return sorted(out.values(), key=lambda c: c["t"])


def _song_sec(a: float, b: float, song: tuple[float, float] | None) -> float:
    if not song:
        return 0.0
    return max(0.0, min(b, song[1]) - max(a, song[0]))


def plan_parts(duration: float, *, beats: list[float], downbeats: list[float] | None = None,
               phrase_every: int = PHRASE_EVERY, events: Any = None, song: tuple[float, float] | None = None,
               song_offset: float = 0.0, song_breaks: Iterable[float] = (), max_song_sec: float = MAX_SONG_SEC,
               min_part_sec: float = MIN_PART_SEC, parts: int | None = None, label_text: str = LABEL_TEXT,
               next_text: str = NEXT_TEXT, balance_weight: float = BALANCE_WEIGHT) -> dict:
    """План частей ролика длиной ``duration`` (секунды готового ролика).

    ``song`` — (начало, конец) звучания песни в ролике (None — песни нет: части только по длине и боям);
    ``song_offset`` — секунда трека в начале ``song``; ``song_breaks`` — где трек начинается заново с начала (повтор).
    ``beats``/``downbeats`` — доли в секундах ролика; ``events`` — бои ``[{start, end}]``; ``balance_weight`` — насколько
    важнее равная длина частей, чем лучший стык (``EQUAL_WEIGHT`` — части одной длины, работа «Мувик под песню»).
    Ответ: ``{count, parts: [{index, label, start, end, duration, song, cut, layers, caption_suffix}], ...}``.
    ValueError — так нарезать нельзя (бой длиннее ``max_song_sec`` песни, нет стыка в просвете)."""
    duration = float(duration)
    if duration <= 0:
        raise ValueError("у ролика нет длины")
    if not 10.0 <= max_song_sec <= 60.0:
        raise ValueError("max_song_sec — 10..60 с (TikTok глушит больше 60 с одной песни)")
    spans = _spans(events)
    if song:
        song = (max(0.0, float(song[0])), min(duration, float(song[1])))
        if song[1] - song[0] <= EPS:
            song = None
    breaks = sorted({round(float(t), 3) for t in song_breaks if EPS < float(t) < duration - EPS})
    for a, b in spans:
        if _song_sec(a, b, song) > max_song_sec + EPS:
            raise ValueError(f"бой {a:.1f}–{b:.1f} с идёт под песню {_song_sec(a, b, song):.1f} с — больше "
                             f"{max_song_sec:g} с: целиком в часть не влезает. Сократи бой (вырежи пробежку внутри) "
                             "или раздели его по смыслу сам")
        inner = [t for t in breaks if a + EPS < t < b - EPS]
        if inner:
            raise ValueError(f"песня начинается заново на {inner[0]:.1f} с — посреди боя {a:.1f}–{b:.1f} с; сдвинь "
                             "музыку (music_start_sec) или бой")

    cands = boundary_candidates(duration, beats=beats, downbeats=downbeats, phrase_every=phrase_every, events=spans,
                                song=song, song_breaks=breaks)
    nodes = [{"t": 0.0, "kind": "start", "cost": 0.0}, *cands, {"t": duration, "kind": "end", "cost": 0.0}]
    times = [n["t"] for n in nodes]
    n_nodes = len(nodes)
    short = min(min_part_sec, duration)
    break_set = breaks

    def ok(i: int, j: int) -> bool:
        a, b = times[i], times[j]
        if b - a < short - EPS:
            return False
        if _song_sec(a, b, song) > max_song_sec + EPS:
            return False
        k = bisect.bisect_right(break_set, a + EPS)
        return not (k < len(break_set) and break_set[k] < b - EPS)

    def preds(j: int) -> Iterable[int]:
        # Песни в части только прибавляется с ранним началом: дальше лимита — дальше не смотрим.
        for i in range(j - 1, -1, -1):
            if _song_sec(times[i], times[j], song) > max_song_sec + EPS:
                break
            if ok(i, j):
                yield i

    # 1) наименьшее число частей
    inf = math.inf
    fewest = [inf] * n_nodes
    fewest[0] = 0
    for j in range(1, n_nodes):
        best = min((fewest[i] + 1 for i in preds(j) if fewest[i] < inf), default=inf)
        fewest[j] = best
    if fewest[-1] == inf:
        raise ValueError(_why_impossible(times, song, max_song_sec, spans))
    count = int(fewest[-1])
    if parts is not None and int(parts) > count:
        count = int(parts)

    # 2) ровно count частей: близкие длины и лучшие стыки
    target = duration / count
    cost = [[inf] * n_nodes for _ in range(count + 1)]
    back = [[-1] * n_nodes for _ in range(count + 1)]
    cost[0][0] = 0.0
    for k in range(1, count + 1):
        for j in range(1, n_nodes):
            if k == count and j != n_nodes - 1:
                continue
            for i in preds(j):
                if cost[k - 1][i] == inf:
                    continue
                length = times[j] - times[i]
                c = cost[k - 1][i] + balance_weight * ((length - target) / target) ** 2 + nodes[j]["cost"]
                if c < cost[k][j] - 1e-12:
                    cost[k][j], back[k][j] = c, i
    if cost[count][-1] == inf:
        raise ValueError(f"на {count} частей не делится (части короче {min_part_sec:g} с или песни больше "
                         f"{max_song_sec:g} с) — попроси меньше частей или убери parts")
    path = [n_nodes - 1]
    for k in range(count, 0, -1):
        path.append(back[k][path[-1]])
    path.reverse()

    out_parts = []
    notes = []
    for n, (i, j) in enumerate(zip(path, path[1:]), start=1):
        a, b = times[i], times[j]
        part = _part(n, count, a, b, nodes[j], song, song_offset, breaks, label_text, next_text)
        if nodes[j]["kind"] == "gap":
            notes.append(f"стык частей {n} и {n + 1} на {b:.2f} с — не на доле (в просвете между боями долей нет): "
                         "проверь на слух, при нужде сдвинь бой или музыку")
        out_parts.append(part)
    return {"count": count, "duration": round(duration, 3), "max_song_sec": max_song_sec,
            "song": {"start": round(song[0], 3), "end": round(song[1], 3), "sec": round(song[1] - song[0], 3)}
            if song else None,
            "events": [{"start": round(a, 3), "end": round(b, 3)} for a, b in spans], "parts": out_parts,
            "notes": notes, "time": "final"}


def _part(n: int, m: int, a: float, b: float, end_node: dict, song: tuple[float, float] | None, offset: float,
          breaks: list[float], label_text: str, next_text: str) -> dict:
    length = round(b - a, 3)
    sec = _song_sec(a, b, song)
    song_info = None
    if song and sec > EPS:
        s0 = max(a, song[0])
        # Секунда трека: от начала песни (с ``offset``) или от последнего повтора трека перед частью (с нуля).
        later = [t for t in breaks if song[0] + EPS < t <= s0 + EPS]
        track_at = (s0 - later[-1]) if later else offset + (s0 - song[0])
        song_info = {"from": round(s0, 3), "to": round(min(b, song[1]), 3), "sec": round(sec, 3),
                     "track_start": round(track_at, 3), "track_end": round(track_at + sec, 3)}
    label = label_text.format(n=n, m=m)
    layers = [{"type": "text", "start": 0.0, "end": round(min(LABEL_SEC, length), 3), "x": 0.5, "y": 0.13,
               "props": {"text": label, "size": 64}}]
    end_text = None
    if n < m:
        end_text = next_text.format(n=n, m=m, next=n + 1)
        layers.append({"type": "text", "start": round(max(0.0, length - END_TEXT_SEC), 3), "end": length, "x": 0.5,
                       "y": 0.42, "props": {"text": end_text, "size": 72}})
    return {"index": n, "label": label, "start": round(a, 3), "end": round(b, 3), "duration": length,
            "song": song_info, "cut": {"at": round(b, 3), "kind": end_node["kind"]}, "end_text": end_text,
            "layers": layers, "caption_suffix": f" · {n}/{m}"}


def _why_impossible(times: list[float], song: tuple[float, float] | None, max_song_sec: float,
                    spans: list[tuple[float, float]]) -> str:
    gaps = [(a, b) for a, b in zip(times, times[1:]) if _song_sec(a, b, song) > max_song_sec + EPS]
    if gaps:
        a, b = gaps[0]
        return (f"между {a:.1f} и {b:.1f} с нет ни одной доли вне боя, а песни там {_song_sec(a, b, song):.1f} с "
                f"> {max_song_sec:g} с — стык поставить некуда. Передай сетку долей (beats или bpm) или события поуже")
    return "части не складываются: слишком короткий ролик для min_part_sec или песни больше лимита"


def grid_lists(grid: dict, duration: float) -> tuple[list[float], list[float]]:
    """Сетка ``{period|bpm, first_beat, first_downbeat, downbeat_every}`` (как ``audio_map.music_beats``) →
    (доли, сильные доли) на [0, duration]."""
    from app.beat_snap import grid_times

    return grid_times(grid, duration, "beat"), grid_times(grid, duration, "downbeat")


__all__ = ["MAX_SONG_SEC", "MIN_PART_SEC", "boundary_candidates", "grid_lists", "phrase_marks", "plan_parts"]
