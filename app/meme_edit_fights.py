"""Мем-эдит: бои целиком (чистые функции — их проверяют тесты).

Что одобрил владелец (пилот 2, D:/vvf-offload/memes/pilot2/storyboard.md, v3): **блок = весь бой** — от первого
контакта (первые выстрелы / первый урон, за 1–2 с до них) до конца боя и реакции игрока; внутри вырезано только мёртвое
время (бег, лут, ползёт в ноке — без стрельбы и без речи дольше ~3 с). Начало боя не режется никогда; не влезает в
длину — выкидывается слабый бой целиком. Отвергнуто: «только конец киллов без начала стрельбы», «потерян дэмедж в
начале файта».

* ``find_fights`` — бои записи из событий игры (app/game_events: knock, kill, death, squad_wipe, win, shot_burst,
  reaction), плотных выстрелов звука игры (``shots`` — отрезки стрельбы) и слов микрофона/тиммейтов;
* ``score_fight`` — сила боя: вайп, победа, киллы, смерть (фейл — тоже смешно), реплики игрока и реакции;
* ``select_fights`` — лучшие бои до целевой длины; ``order_fights`` — порядок ролика (быстрый понятный бой первым,
  победа последней, сильнейший разворот перед ней, победа и фейл через раз);
* ``pick_vertical`` — «лучшее» 9:16 на 60–90 с из трёх сильнейших боёв, которые влезают.

Время — секунды исходника.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

COMBAT = ("shot_burst", "knock", "kill", "death", "squad_wipe", "win", "ult")
DECISIVE = ("knock", "kill", "death", "squad_wipe", "win")
FIGHT_GAP = 10.0        # стрельба/события ближе — один бой
LEAD = 1.5              # запас до первого контакта
TAIL = 1.2              # после последнего решающего события
REACTION_TAIL = 5.0     # реплика игрока, начатая не позже стольких секунд после боя, — его реакция
MAX_REACTION_SEC = 9.0  # хвост реакции не длиннее
DEAD_GAP = 3.0          # тишина (ни выстрелов, ни событий, ни слов) дольше — мёртвое время, вырезается
KEEP_AFTER = 0.8        # от мёртвого времени остаётся хвост после действия …
KEEP_BEFORE = 0.6       # … и разгон перед следующим
MIN_PIECE = 1.9         # кусок короче не оставляем (минимум куска сервиса 1.5 с + запас на сдвиг в долю)
MAX_PIECE = 170.0
WEIGHT = {"win": 4.0, "squad_wipe": 2.5, "kill": 1.2, "knock": 1.0, "death": 1.0, "max_level": 0.2,
          "level_up": 0.1, "ult": 0.2, "reaction": 0.4}
MEME_SHARE = 0.08       # мемы добавляют к длине боёв примерно столько (1 мем ~1.8 с на 22 с)
MIN_SCORE = 1.0         # бой без решающего события и без реплик — не берём
NO_EVENT_BURSTS = 8     # без решающего события бой — только при такой плотной стрельбе (детектор мог не увидеть нок)
JOIN_GAP = 8.0          # бои одной записи ближе — один бой (A05: нок → ползёт → «бочка кончилась» → смерть)


@dataclass
class Fight:
    source_id: int
    start: float
    end: float
    pieces: list[list[float]] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)       # реплики игрока (микрофон) внутри боя
    team_lines: list[dict] = field(default_factory=list)  # реплики тиммейтов (звук игры)
    score: float = 0.0
    result: str = "none"  # win | wipe | kill | death | none

    @property
    def length(self) -> float:
        return round(sum(b - a for a, b in self.pieces), 3)

    @property
    def key(self) -> str:
        return f"s{self.source_id}@{self.start:.1f}"

    def brief(self) -> dict:
        kinds: dict[str, int] = {}
        for e in self.events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {"key": self.key, "source_id": self.source_id, "start": round(self.start, 2), "end": round(self.end, 2),
                "length": self.length, "pieces": [[round(a, 2), round(b, 2)] for a, b in self.pieces],
                "score": round(self.score, 2), "result": self.result, "events": kinds,
                "lines": [ln["text"] for ln in self.lines][:12]}


def lines_from_words(words: list[dict] | None, gap: float = 0.6) -> list[dict]:
    """Слова → реплики ``{start, end, text, words}``: пауза больше ``gap`` или конец предложения — новая реплика."""
    out: list[dict] = []
    for w in sorted((w for w in words or [] if str(w.get("word") or "").strip()), key=lambda w: float(w["start"])):
        a, b = float(w["start"]), float(w["end"])
        last = out[-1] if out else None
        if last and a - last["end"] <= gap and not str(last["words"][-1]["word"]).rstrip().endswith((".", "!", "?")):
            last["end"] = max(last["end"], b)
            last["words"].append(w)
        else:
            out.append({"start": a, "end": b, "words": [w]})
    for ln in out:
        ln["text"] = " ".join(str(w["word"]).strip() for w in ln["words"])
        ln["start"], ln["end"] = round(ln["start"], 3), round(ln["end"], 3)
    return out


def _items(events: list[dict], shots: list[list[float]]) -> list[tuple[float, float, dict | None]]:
    out = [(float(a), float(b), None) for a, b in shots]
    for e in events:
        if e["kind"] in COMBAT:
            t = float(e["t"])
            out.append((t, t + (1.0 if e["kind"] == "shot_burst" else 0.3), e))
    return sorted(out, key=lambda x: x[0])


def _inside_word(t: float, words: list[dict]) -> dict | None:
    return next((w for w in words if float(w["start"]) < t < float(w["end"])), None)


def activity(events: list[dict], shots: list[list[float]], words: list[dict]) -> list[list[float]]:
    """Где в бою что-то происходит: стрельба, события, речь (любого голоса) — отрезки с запасом, склеенные."""
    spans = [[a - 0.3, b + 0.3] for a, b in shots]
    spans += [[float(e["t"]) - 0.6, float(e["t"]) + 1.2] for e in events if e["kind"] != "reaction"]
    spans += [[float(e["t"]) - 0.3, float(e["t"]) + 1.2] for e in events if e["kind"] == "reaction"]
    spans += [[float(w["start"]) - 0.2, float(w["end"]) + 0.3] for w in words]
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def dead_time_pieces(start: float, end: float, act: list[list[float]], gap: float = DEAD_GAP,
                     min_piece: float = MIN_PIECE) -> list[list[float]]:
    """Куски боя без мёртвого времени: промежуток без активности дольше ``gap`` вырезается (остаются хвост
    KEEP_AFTER после действия и разгон KEEP_BEFORE перед следующим). Кусок короче ``min_piece`` — вырез отменяется."""
    inside = [[max(start, a), min(end, b)] for a, b in act if b > start and a < end]
    cuts: list[list[float]] = []
    prev = start
    for a, b in inside:
        if a - prev > gap:
            ca, cb = prev + KEEP_AFTER, a - KEEP_BEFORE
            if cb - ca >= 1.0:
                cuts.append([ca, cb])
        prev = max(prev, b)
    if end - prev > gap:  # хвост без действия в конце боя (конец боя ставит find_fights, сюда редко доходит)
        cuts.append([prev + KEEP_AFTER, end])
    pieces: list[list[float]] = []
    cur = start
    for ca, cb in cuts:
        pieces.append([cur, ca])
        cur = cb
    if end - cur > 0.05:
        pieces.append([cur, end])
    # короткие куски — вырез рядом отменяется (кусок склеивается с соседом)
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        for i, (a, b) in enumerate(pieces):
            if b - a >= min_piece:
                continue
            j = i - 1 if i > 0 else i + 1
            lo, hi = sorted((i, j))
            pieces[lo:hi + 1] = [[pieces[lo][0], pieces[hi][1]]]
            changed = True
            break
    # длиннее предела куска сервиса — пополам
    out: list[list[float]] = []
    for a, b in pieces:
        while b - a > MAX_PIECE:
            out.append([a, a + MAX_PIECE / 2])
            a += MAX_PIECE / 2
        out.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in out]


def score_fight(fight: Fight) -> tuple[float, str]:
    """Сила боя и его итог. Решающие события — по весам WEIGHT (оценка детектора — множитель), реплики игрока
    (до 4) и реакции голосом (до 3) — бонус; длинный бой без событий не выигрывает длиной."""
    kinds = [e["kind"] for e in fight.events]
    total = 0.0
    reactions = 0
    for e in fight.events:
        w = WEIGHT.get(e["kind"], 0.0)
        if e["kind"] == "reaction":
            reactions += 1
            if reactions > 3:
                continue
        total += w * min(1.0, max(0.3, float(e.get("score", 0.8))))
    total += 0.45 * min(4, len(fight.lines))
    loud = sum(1 for ln in fight.lines if any(ch in ln["text"] for ch in "!?"))
    total += 0.2 * min(3, loud)
    if "win" in kinds:
        result = "win"
    elif "squad_wipe" in kinds:
        result = "wipe"
    elif any(k in kinds for k in ("kill", "knock", "death")):
        # итог — последнее решающее событие: нокнул и умер — фейл, умер тиммейт и добил — победа
        last = [k for k in kinds if k in ("kill", "knock", "death")][-1]
        result = "death" if last == "death" else "kill"
    else:
        result = "none"
    return round(total, 3), result


def find_fights(source_id: int, events: list[dict], shots: list[list[float]] | None, owner_words: list[dict] | None,
                team_words: list[dict] | None, duration: float, *, dead_gap: float = DEAD_GAP) -> list[Fight]:
    """Бои записи целиком: стрельба и события ближе FIGHT_GAP — один бой; бой без решающего события и без
    плотной стрельбы (меньше 3 очередей) — не бой. Начало — первый контакт − LEAD (и раньше, если игрок в этот
    момент говорит: колл «спина!» — часть боя), конец — последнее решающее событие + TAIL, но не раньше конца
    стрельбы, плюс реакция игрока (реплика, начатая до REACTION_TAIL после боя). Внутри — без мёртвого времени."""
    events = sorted(events or [], key=lambda e: float(e["t"]))
    owner = sorted(owner_words or [], key=lambda w: float(w["start"]))
    team = sorted(team_words or [], key=lambda w: float(w["start"]))
    every = owner + team
    groups: list[list[tuple[float, float, dict | None]]] = []
    for item in _items(events, shots or []):
        if groups and item[0] - max(x[1] for x in groups[-1]) <= FIGHT_GAP:
            groups[-1].append(item)
        else:
            groups.append([item])
    fights: list[Fight] = []
    for g in groups:
        evs = [x[2] for x in g if x[2] is not None]
        dec = [e for e in evs if e["kind"] in DECISIVE]
        bursts = sum(1 for x in g if x[2] is None or x[2]["kind"] == "shot_burst")
        if not dec and bursts < NO_EVENT_BURSTS:
            continue  # без нока, килла, смерти — не бой (разговор на высадке, стрельба по пустоте)
        start = max(0.0, g[0][0] - LEAD)
        # колл перед боем: реплика, идущая в момент начала (или кончившаяся меньше чем за 1 с до него)
        for ln in lines_from_words(every):
            if ln["start"] < start <= ln["end"] + 1.0 and start - ln["start"] < 6.0:
                start = max(0.0, ln["start"] - 0.25)
        last_dec = max((float(e["t"]) for e in dec), default=g[-1][0])
        end = max(last_dec + TAIL, max(x[1] for x in g) + 0.5)
        limit = end + MAX_REACTION_SEC
        for ln in lines_from_words(owner):
            if end - 0.5 <= ln["start"] <= end + REACTION_TAIL and ln["end"] + 0.4 <= limit:
                end = max(end, ln["end"] + 0.4)
        hit = _inside_word(end, every)
        if hit:
            end = float(hit["end"]) + 0.2
        end = min(float(duration), end)
        if end - start < 3.0:
            continue
        inside = [e for e in events if start <= float(e["t"]) <= end]
        f_words = [w for w in every if float(w["end"]) > start and float(w["start"]) < end]
        act = activity(inside, [s for s in shots or [] if s[1] > start and s[0] < end], f_words)
        fight = Fight(int(source_id), round(start, 3), round(end, 3),
                      pieces=dead_time_pieces(start, end, act, gap=dead_gap), events=inside,
                      lines=[ln for ln in lines_from_words(owner) if start <= ln["start"] < end],
                      team_lines=[ln for ln in lines_from_words(team) if start <= ln["start"] < end])
        fight.score, fight.result = score_fight(fight)
        fights.append(fight)
    # бои, задевшие друг друга запасами или идущие почти подряд, — один бой (склейка; пауза станет мёртвым временем)
    merged: list[Fight] = []
    for f in fights:
        if merged and f.start <= merged[-1].end + JOIN_GAP:
            m = merged[-1]
            m.end = max(m.end, f.end)
            m.events = [e for e in events if m.start <= float(e["t"]) <= m.end]
            m.lines = [ln for ln in lines_from_words(owner) if m.start <= ln["start"] < m.end]
            m.team_lines = [ln for ln in lines_from_words(team) if m.start <= ln["start"] < m.end]
            f_words = [w for w in every if float(w["end"]) > m.start and float(w["start"]) < m.end]
            act = activity(m.events, [s for s in shots or [] if s[1] > m.start and s[0] < m.end], f_words)
            m.pieces = dead_time_pieces(m.start, m.end, act, gap=dead_gap)
            m.score, m.result = score_fight(m)
        else:
            merged.append(f)
    return merged


def with_memes(length: float) -> float:
    return length * (1.0 + MEME_SHARE)


def select_fights(fights: list[Fight], target_sec: float, min_score: float = MIN_SCORE) -> tuple[list[Fight], list[str]]:
    """Лучшие бои до ``target_sec`` (с мемами): по силе на корень длины (длинный бой не берёт длиной), слабее
    ``min_score`` — нет; бой целиком или никак (начала не режем). Ответ — (бои, заметки)."""
    notes: list[str] = []
    good = [f for f in fights if f.score >= min_score and f.length >= 4.0]
    weak = len(fights) - len(good)
    if weak:
        notes.append(f"слабых боёв отброшено: {weak} (нет решающего события и реплик)")
    ranked = sorted(good, key=lambda f: -f.score / max(1.0, (f.length / 30.0)) ** 0.5)
    chosen: list[Fight] = []
    used = 0.0
    for f in ranked:
        if used + with_memes(f.length) <= target_sec + 4.0:
            chosen.append(f)
            used += with_memes(f.length)
    left = [f for f in ranked if f not in chosen]
    if left:
        notes.append(f"не влезли в {target_sec:.0f} с: {len(left)} боёв (бои не режутся — выкинуты целиком)")
    if not chosen and ranked:
        chosen = [min(ranked, key=lambda f: f.length)]
        notes.append(f"самый короткий сильный бой {chosen[0].length:.0f} с длиннее цели — взят он один")
    if used and used < target_sec * 0.6:
        notes.append(f"сильных боёв набралось на {used:.0f} с из {target_sec:.0f} — добивать слабыми не стал")
    return chosen, notes


def _victory(f: Fight) -> bool:
    return f.result in ("win", "wipe", "kill")


def order_fights(fights: list[Fight]) -> list[Fight]:
    """Порядок ролика (пилот 2): первым — быстрый понятный бой (сила на секунду, ≤ 25 с), последним — победа
    (или сильнейший), перед ним — следующий по силе; середина — победа и фейл через раз, по времени записи."""
    rest = list(fights)
    if len(rest) <= 1:
        return rest
    wins = [f for f in rest if f.result == "win"]
    closer = max(wins or rest, key=lambda f: f.score)
    rest.remove(closer)
    short = [f for f in rest if f.length <= 25.0] or rest
    opener = max(short, key=lambda f: f.score / max(5.0, f.length))
    rest.remove(opener)
    before = None
    if rest:
        before = max(rest, key=lambda f: f.score)
        rest.remove(before)
    rest.sort(key=lambda f: (f.source_id, f.start))
    vic = [f for f in rest if _victory(f)]
    fail = [f for f in rest if not _victory(f)]
    middle: list[Fight] = []
    want_fail = _victory(opener)
    while vic or fail:
        pool = fail if (want_fail and fail) or not vic else vic
        middle.append(pool.pop(0))
        want_fail = _victory(middle[-1])
    return [opener, *middle, *([before] if before else []), closer]


def pick_vertical(fights: list[Fight], *, max_sec: float = 90.0, min_sec: float = 60.0, count: int = 3) -> list[Fight]:
    """«Лучшее» 9:16: до ``count`` сильнейших боёв вместе ≤ ``max_sec`` (с мемами); больше сумма силы — лучше,
    из равных — ближе к ``min_sec``…``max_sec``. Ничего не влезает — самый сильный из влезающих в одиночку, иначе
    самый короткий."""
    pool = sorted(fights, key=lambda f: -f.score)[:8]
    best: tuple[float, list[Fight]] = (-1.0, [])
    for n in range(min(count, len(pool)), 0, -1):
        for combo in itertools.combinations(pool, n):
            length = sum(with_memes(f.length) for f in combo)
            if length > max_sec:
                continue
            value = sum(f.score for f in combo) + (1.0 if length >= min_sec else 0.0) + 0.5 * n
            if value > best[0]:
                best = (value, list(combo))
        if best[1] and len(best[1]) == n:
            break
    if not best[1] and pool:
        best = (0.0, [min(pool, key=lambda f: f.length)])
    return order_fights(best[1])


def briefs(fights: list[Fight]) -> list[dict[str, Any]]:
    return [f.brief() for f in fights]
