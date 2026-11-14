"""Мем-эдит: раскладка по времени (чистые функции, без базы и медиа — их проверяют тесты).

Из событий игры (app/meme_edit_events.py), сетки долей музыки и библиотеки видеомемов собирается список **окон**
исходника — что из записи попадёт в ролик, по порядку:

* ``chop`` — рубленый кусочек геймплея 1–3 доли (0.45–1.6 с) перед моментом: «разгон» эдита;
* ``hit`` — сам момент: событие (нок, килл, смерть) ровно на долю — окно начинается за целое число долей до него;
* ``voice`` — реакция в микрофон: окно длиннее (фразу слышно целиком, с субтитрами);
* ``meme`` — слот под врезку видеомема: геймплей под ним не виден (вставка во весь кадр), длина — мем, округлённый
  до доли, так что склейка обратно в игру тоже в долю.

Все окна — целое число долей (кроме подрезанных у краёв записи), поэтому каждая склейка готового ролика ложится на
долю музыки, если музыка стартует с сильной доли (app/meme_edit.py ставит ``music_start_sec`` так).

Дальше окна превращаются в куски клипа и вырезы (``to_pieces``): соседние окна — один кусок исходника, промежутки —
``render.cuts``. Шкалы: source (исходник), clip (куски подряд, до вырезов; переходов нет), final (готовый ролик).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from app.clip_spec import MAX_PIECES  # один предел кусков на весь сервис

KIND_WEIGHT = {"win": 1.0, "squad_wipe": 0.95, "kill": 0.85, "knock": 0.75, "death": 0.7, "teammate_fail": 0.65,
               "reaction": 0.6, "max_level": 0.5, "ult": 0.45, "level_up": 0.4, "shot_burst": 0.25}
# Событие → теги видеомема (search_video_memes): первый тег главный.
KIND_TAGS = {
    "kill": ("победа", "хвастовство", "шок", "уважение", "вау"),
    "knock": ("шок", "победа", "вау", "хвастовство"),
    "squad_wipe": ("победа", "хвастовство", "уважение", "вау", "шок"),
    "win": ("победа", "радость", "хвастовство", "уважение"),
    "death": ("фейл", "грусть", "злость", "кринж"),
    "teammate_fail": ("тиммейт", "фейл", "кринж", "непонимание"),
    "reaction": ("смех", "радость", "крик", "шок"),
    "level_up": ("вау", "радость", "уважение"),
    "max_level": ("вау", "уважение", "радость"),
    "ult": ("вау", "шок"),
    "shot_burst": ("шок", "вау"),
}
NEGATIVE_KINDS = ("death", "teammate_fail")
# Мультяшные слова (стиль «мульт-жёлтый»): геймерский сленг, коротко, как в рефе («Джампнул», «Видели?», «Рокет»).
KIND_WORDS = {
    "kill": ("Минус один", "Убрал", "Изи", "Чисто"),
    "knock": ("Нокнул", "Есть!", "Попал", "Лёг"),
    "squad_wipe": ("Вайп", "Минус сквад", "Все легли"),
    "win": ("Чемпион", "Топ-1", "Взяли"),
    "death": ("Ну всё", "Помянем", "Обидно"),
    "teammate_fail": ("Тиммейт…", "Спасибо"),
    "reaction": ("Видели?", "Ахах"),
    "level_up": ("Ап!",),
    "max_level": ("Макс",),
    "ult": ("Ульта!",),
    "shot_burst": ("Тра-та-та",),
}
BIG_KINDS = ("win", "squad_wipe", "kill", "knock")
CHOP_BEATS = (2, 2, 3, 1, 2, 3, 4)
CHOP_MIN_SEC, CHOP_MAX_SEC = 0.45, 1.6
LEAD_SPAN_SEC = 10.0  # откуда берём рубку перед моментом
MOMENT_SEP_SEC = 5.0
EST_MOMENT_SEC = 7.0
MAX_MOMENTS = 10  # моментов с мемом в одном авто-эдите (вставок в клипе до app/montage_assets.MAX_INSERTS)
MEME_MAX_SEC = 4.0  # полнокадровая врезка мема — до 4 с (app/video_memes.MEME_FULL_MAX_SEC)
MEME_SOFT_SEC = 3.0  # реф: 0.7–3 с; длиннее — ниже в выборе
WORD_MIN_SEC = 0.9
NO_TEXT_BEFORE_SEC = 2.5  # в начале эдита текста нет (как в рефе)
MAX_PIECE_SEC = 180.0
MIN_PIECE_SEC = 1.5
MIN_CUT_SEC = 0.08
DEFAULT_PERIOD = 0.5  # 120 BPM, если у музыки не нашлось пульса
MIN_CHOP_START_SEC = 5.0  # начало записи (загрузка, лобби, «стоим у двери») рубкой не берём
MIN_MOTION = 0.25  # доля «движения» (90-й перцентиль записи = 1): ниже — статичный кадр, рубку туда не ставим


def _always(_t: float) -> float:
    return 1.0


@dataclass
class Context:
    """Что известно о картинке записи (app/meme_edit_visual.py): движение по времени, серый экран смерти/зрителя."""
    motion: Any = _always
    avoid: list[tuple[float, float]] = field(default_factory=list)
    min_start: float = MIN_CHOP_START_SEC


@dataclass
class Window:
    start: float  # секунды исходника
    end: float
    role: str  # chop | hit | voice | meme
    event: dict | None = None
    meme: dict | None = None
    insert_sec: float = 0.0
    big: bool = False
    word: str = ""
    clip_start: float = 0.0  # заполняет to_pieces
    final_start: float = 0.0

    @property
    def length(self) -> float:
        return round(self.end - self.start, 3)


@dataclass
class Timeline:
    windows: list[Window] = field(default_factory=list)
    period: float = DEFAULT_PERIOD
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(w.length for w in self.windows), 3)


def weight(event: dict) -> float:
    return KIND_WEIGHT.get(event.get("kind"), 0.3) * (0.5 + 0.5 * float(event.get("score", 0.5)))


def _beats(sec: float, period: float, lo: int = 1) -> int:
    return max(lo, int(round(sec / period)))


def meme_score(meme: dict, kind: str) -> float:
    """Насколько мем подходит событию: поиск видеомемов (app/video_memes.score) по тегам события, главный тег весомее;
    длиннее ``MEME_SOFT_SEC`` — ниже."""
    from app.video_memes import score

    tags = KIND_TAGS.get(kind) or KIND_TAGS["shot_burst"]
    total = sum(score(meme, tag) * (1.0 if i == 0 else 0.45) for i, tag in enumerate(tags))
    if float(meme.get("duration_sec") or 0) > MEME_SOFT_SEC:
        total *= 0.6
    return total


def pick_meme(library: list[dict], kind: str, used: set[int], rng: random.Random) -> dict | None:
    """Мем под событие: не повторяется, одобренный (библиотеку фильтрует вызывающий), из трёх лучших — по жребию."""
    scored = [(meme_score(m, kind), m) for m in library
              if int(m["meme_id"]) not in used and 0.3 <= float(m.get("duration_sec") or 0) <= MEME_MAX_SEC]
    scored = [pair for pair in scored if pair[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda p: (-p[0], int(p[1]["meme_id"])))
    top = scored[:3]
    best = top[0][0]
    top = [p for p in top if p[0] >= best * 0.7]
    return rng.choice(top)[1] if len(top) > 1 else top[0][1]


def choose_moments(events: list[dict], count: int, sep: float = MOMENT_SEP_SEC) -> list[dict]:
    """Главные события (не просто выстрелы), сильные первыми, не ближе ``sep`` друг к другу; потом — по времени.
    Не хватает — добор самыми сильными выстрелами (запись без детектора событий)."""
    pool = [e for e in events if e["kind"] != "shot_burst"]
    if len(pool) < count:  # главных событий мало — добираем самыми сильными выстрелами
        shots = sorted((e for e in events if e["kind"] == "shot_burst"), key=lambda e: -float(e.get("score", 0)))
        pool += [e for e in shots if float(e.get("score", 0)) >= 0.5 or not pool][: count - len(pool) + 2]
    picked: list[dict] = []
    for ev in sorted(pool, key=lambda e: (-weight(e), e["t"])):
        if all(abs(ev["t"] - p["t"]) >= sep for p in picked):
            picked.append(ev)
        if len(picked) >= count:
            break
    return sorted(picked, key=lambda e: e["t"])


def _inside(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a - 1e-6 <= t <= b + 1e-6 for a, b in spans)


def _lead_chops(events: list[dict], lo: float, hi: float, count: int, period: float,
                rng: random.Random, ctx: "Context | None" = None) -> list[Window]:
    """Рубка перед моментом: кусочки по 1–4 доли в [lo, hi], по времени, без нахлёста. Кандидаты — удары и сетка
    каждые 0.5 с; не раньше ``ctx.min_start`` (заставка, лобби), не на сером экране смерти (``ctx.avoid``), без
    кадров без движения (дверь, пустой коридор: ``ctx.motion`` ниже ``MIN_MOTION``)."""
    ctx = ctx or Context()
    lo = max(lo, ctx.min_start)
    if count <= 0 or hi - lo < CHOP_MIN_SEC:
        return []

    def ok(t: float) -> bool:
        return not _inside(t, ctx.avoid) and ctx.motion(t) >= MIN_MOTION

    hits = sorted((e for e in events if lo + 0.2 <= e["t"] <= hi - 0.2 and ok(e["t"])),
                  key=lambda e: -weight(e) * (0.5 + ctx.motion(e["t"])))
    grid = [lo + 0.3 + k * 0.5 for k in range(int(max(0.0, hi - lo - 0.6) / 0.5) + 1)]
    grid = sorted((t for t in grid if ok(t)), key=lambda t: -ctx.motion(t))
    chosen: list[float] = []
    for t in [e["t"] for e in hits] + grid:  # сначала удары, потом самое подвижное из окна
        if all(abs(t - c) >= 1.2 for c in chosen):
            chosen.append(t)
        if len(chosen) >= count:
            break
    out: list[Window] = []
    cursor = lo
    for t in sorted(chosen):
        beats = rng.choice(CHOP_BEATS)
        length = beats * period
        while length > CHOP_MAX_SEC and beats > 1:
            beats -= 1
            length = beats * period
        if length < CHOP_MIN_SEC:
            beats = _beats(CHOP_MIN_SEC, period)
            length = beats * period
        start = max(cursor, t - 0.4 * length)
        end = start + length
        if end > hi:
            start, end = hi - length, hi
        if start < cursor - 1e-6 or end - start < CHOP_MIN_SEC - 1e-6 or _inside(start, ctx.avoid) \
                or _inside(end, ctx.avoid):
            continue
        ev = next((e for e in events if start <= e["t"] <= end), None)
        out.append(Window(round(start, 3), round(end, 3), "chop", event=ev))
        cursor = end
    return out


def build_timeline(events: list[dict], *, duration: float, period: float, target_sec: float,
                   library: list[dict], seed: int = 0, chops_per_moment: int = 3,
                   moments: int | None = None, words: int = 3, ctx: Context | None = None) -> Timeline:
    """Окна эдита по событиям. ``moments`` — сколько моментов (нет — по длине ``target_sec``)."""
    period = float(period) if period and period > 0.2 else DEFAULT_PERIOD
    rng = random.Random(seed)
    count = moments or max(2, min(MAX_MOMENTS, int(round(target_sec / EST_MOMENT_SEC))))
    ctx = ctx or Context()
    # На сером экране смерти нет ни килла, ни нока: там только сама смерть и голос.
    usable = [e for e in events if e["kind"] in ("death", "reaction") or not _inside(e["t"], ctx.avoid)]
    chosen = choose_moments(usable, count)
    timeline = Timeline(period=period)
    if not chosen:
        timeline.warnings.append("в записи не нашлось ни одного события — эдит не из чего собрать")
        return timeline
    big = max(chosen, key=weight)
    word_moments = _word_moments(chosen, words, rng)
    used: set[int] = set()
    cursor = 0.0
    for ev in chosen:
        e = float(ev["t"])
        kind = ev["kind"]
        voice = kind == "reaction"
        pre = _beats(0.5 if voice else 0.9, period)
        post_sec = 2.4 if voice else (1.6 if ev is big else 0.6)
        if id(ev) in word_moments:
            post_sec = max(post_sec, WORD_MIN_SEC)
        post = max(1, int(math.ceil(post_sec / period - 0.2)))
        hs, he = e - pre * period, min(duration, e + post * period)
        if hs < cursor:
            room = int((e - cursor) // period)
            if room < 1:
                timeline.warnings.append(f"момент {e:.1f} с пропущен: вплотную к прошлому")
                continue
            hs = e - room * period
        lead = _lead_chops(events, max(cursor, hs - LEAD_SPAN_SEC), hs, chops_per_moment, period, rng, ctx)
        timeline.windows.extend(lead)
        hit = Window(round(hs, 3), round(he, 3), "voice" if voice else "hit", event=ev, big=ev is big,
                     word=_word_for(kind, rng, timeline) if id(ev) in word_moments else "")
        timeline.windows.append(hit)
        cursor = hit.end
        meme = pick_meme(library, kind, used, rng)
        if meme is None:
            timeline.warnings.append(f"к событию {kind} на {e:.1f} с нет подходящего одобренного видеомема")
            continue
        dur = min(float(meme["duration_sec"]), MEME_MAX_SEC)
        slot_beats = max(1, int(math.ceil(dur / period - 0.25)))
        slot = slot_beats * period
        if cursor + slot > duration:
            slot = duration - cursor
        if slot < 0.5:
            continue
        used.add(int(meme["meme_id"]))
        timeline.windows.append(Window(round(cursor, 3), round(cursor + slot, 3), "meme", event=ev, meme=meme,
                                       insert_sec=round(min(dur, slot), 2)))
        cursor = round(cursor + slot, 3)
    return timeline


def _word_moments(chosen: list[dict], words: int, rng: random.Random) -> set[int]:
    if words <= 0:
        return set()
    pool = chosen[1:] if len(chosen) > 2 else list(chosen)  # первый момент — заставка без текста
    ranked = sorted(pool, key=lambda e: (-weight(e), e["t"]))[: max(words + 1, 2)]
    rng.shuffle(ranked)
    return {id(e) for e in ranked[:words]}


def _word_for(kind: str, rng: random.Random, timeline: Timeline) -> str:
    taken = {w.word for w in timeline.windows if w.word}
    options = [w for w in KIND_WORDS.get(kind, KIND_WORDS["shot_burst"]) if w not in taken]
    return rng.choice(options) if options else ""


def plan_timeline(events: list[dict], *, duration: float, period: float, target_sec: float, library: list[dict],
                  seed: int = 0, words: int = 3, ctx: Context | None = None) -> Timeline:
    """Подбор числа моментов и рубки под длину ``target_sec`` (±15 %): лишние моменты — прочь (слабые первыми),
    не хватает — больше рубки и моментов, пока есть события."""
    target = float(target_sec)
    best: Timeline | None = None
    moments = max(2, min(MAX_MOMENTS, int(round(target / EST_MOMENT_SEC))))
    chops = 3
    for _ in range(10):
        timeline = build_timeline(events, duration=duration, period=period, target_sec=target, library=library,
                                  seed=seed, chops_per_moment=chops, moments=moments, words=words, ctx=ctx)
        if best is None or abs(timeline.total - target) < abs(best.total - target):
            best = timeline
        if target * 0.85 <= timeline.total <= target * 1.15 or not timeline.windows:
            break
        if timeline.total > target * 1.15:
            if chops > 2:
                chops -= 1
            elif moments > 2:
                moments -= 1
            else:
                break
        else:
            if chops < 5:
                chops += 1
            elif moments < MAX_MOMENTS:
                moments += 1
            else:
                break
    assert best is not None
    if best.windows and best.total < target * 0.7:
        best.warnings.append(f"эдит вышел {best.total:.0f} с при цели {target:.0f} с: в записи мало событий")
    return best


# --------------------------------------------------------------------------- #
# Куски и вырезы
# --------------------------------------------------------------------------- #


def to_pieces(timeline: Timeline, duration: float, max_gap: float = 25.0) -> tuple[list[dict], list[dict]]:
    """Окна → куски исходника (до MAX_PIECES, каждый 1.5–180 с) и вырезы ``render.cuts`` (шкала clip) между окнами.
    Заполняет ``clip_start``/``final_start`` у окон."""
    windows = timeline.windows
    if not windows:
        return [], []
    groups = _group(windows, max_gap)
    while len(groups) > MAX_PIECES:
        max_gap *= 1.6
        groups = _group(windows, max_gap)
        if max_gap > MAX_PIECE_SEC:
            raise ValueError(f"окна эдита не укладываются в {MAX_PIECES} кусков — сократите длину")
    pieces: list[dict] = []
    cuts: list[dict] = []
    clip_offset = final_offset = 0.0
    for gi, group in enumerate(groups, start=1):
        start, end = group[0].start, group[-1].end
        if end - start < MIN_PIECE_SEC:
            end = min(duration, start + MIN_PIECE_SEC)
            if end - start < MIN_PIECE_SEC:
                start = max(0.0, end - MIN_PIECE_SEC)
        prev_end = start
        for w in group:
            gap = w.start - prev_end
            if gap >= MIN_CUT_SEC:
                cuts.append({"start": round(clip_offset + prev_end - start, 3),
                             "end": round(clip_offset + w.start - start, 3)})
            elif gap > 1e-6:  # совсем узкий промежуток не вырезаем — окно начинается чуть раньше
                w.start = prev_end
            w.clip_start = round(clip_offset + w.start - start, 3)
            w.final_start = round(final_offset, 3)
            final_offset += w.length
            prev_end = w.end
        tail = end - prev_end
        if tail >= MIN_CUT_SEC:
            cuts.append({"start": round(clip_offset + prev_end - start, 3), "end": round(clip_offset + end - start, 3)})
        pieces.append({"start_sec": round(start, 3), "end_sec": round(end, 3), "title": f"Мем-эдит — {gi}"})
        clip_offset += end - start
    return pieces, cuts


def _group(windows: list[Window], max_gap: float) -> list[list[Window]]:
    groups: list[list[Window]] = []
    for w in windows:
        if groups and w.start - groups[-1][-1].end <= max_gap and w.end - groups[-1][0].start <= MAX_PIECE_SEC:
            groups[-1].append(w)
        else:
            groups.append([w])
    return groups


def source_to_clip(timeline: Timeline, t: float) -> float | None:
    """Секунда исходника → шкала clip (только внутри окон эдита)."""
    for w in timeline.windows:
        if w.start - 1e-6 <= t <= w.end + 1e-6:
            return round(w.clip_start + (t - w.start), 3)
    return None


def final_to_clip(timeline: Timeline, f: float) -> float:
    """Секунда готового ролика → шкала clip (до вырезов)."""
    for w in timeline.windows:
        if f < w.final_start + w.length - 1e-6:
            return round(w.clip_start + max(0.0, f - w.final_start), 3)
    last = timeline.windows[-1]
    return round(last.clip_start + last.length, 3)


def clip_span(timeline: Timeline, final_from: float, final_to: float) -> tuple[float, float]:
    """Отрезок готового ролика → начало и конец в шкале clip (вырезы внутри рендер уберёт сам)."""
    return final_to_clip(timeline, final_from), final_to_clip(timeline, max(final_from + 0.1, final_to) - 1e-4)


def summary(timeline: Timeline) -> list[dict]:
    """Окна для ответа: что, откуда, где в готовом ролике."""
    out = []
    for w in timeline.windows:
        item: dict[str, Any] = {"role": w.role, "source": [w.start, w.end], "final": round(w.final_start, 2),
                                "sec": w.length}
        if w.event:
            item["event"] = {"t": w.event["t"], "kind": w.event["kind"]}
        if w.meme:
            item["meme"] = {"meme_id": w.meme["meme_id"], "title": w.meme.get("title"), "sec": w.insert_sec}
        if w.word:
            item["word"] = w.word
        if w.big:
            item["big"] = True
        out.append(item)
    return out
