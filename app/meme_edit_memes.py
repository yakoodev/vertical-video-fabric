"""Мем-эдит: какие видеомемы и куда (чистые функции — их проверяют тесты).

Правила владельца (пилоты 1–2, D:/vvf-offload/memes/pilot*/storyboard.md):

* мем — **ответ на реплику игрока или событие**, а не на громкость: сетап (фраза) → удар (событие рядом) → мем;
* мем ставится **сразу после сетапа** (конец фразы), лучший — «эхо»: мем повторяет слово игрока («Не надо» → …,
  «Альцгеймер» → «…ужасная болезнь», «158 в голову» → «Emotional damage»); эхо по буквам — ``echo_words``, по
  смыслу (без общих слов) — локальная модель, app/meme_semantic.py (``semantic_z``);
* мемов немного: ~1 на 20–25 с («мемов чересчур много» на 1/10 с), не больше двух на бой, ни одного повтора;
* последний кадр боя — почти всегда мем (реакция на итог боя);
* мем — только удар: 0.8–2.2 с без пауз внутри (app/meme_trim.py), «мемы вставляй недолго — это темп портит».

Вход — бои (app/meme_edit_fights.Fight) и библиотека одобренных мемов (``meme_id, asset_id, title, tags,
description, when_to_use, transcript, transcript_words, duration_sec``). Время — секунды исходника.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.meme_edit_fights import WEIGHT, Fight

MEME_EVERY_SEC = 22.0
MAX_PER_FIGHT = 2
MIN_GAP_SEC = 10.0      # мемы одного боя не ближе (секунды исходника)
MIN_FIT = 2.0           # хуже — мем не по смыслу, момент остаётся без мема
MIN_FIT_LAST = 1.5      # конец боя: мем-ответ на итог (фейл → «Нет.») — хватает главного тега итога
ECHO_POINTS = 4.0
# Смысл (app/meme_semantic.line_scores): z-оценка мема среди библиотеки на эту реплику. Ниже SEM_FLOOR σ — шум,
# выше — SEM_WEIGHT очков за σ (до SEM_CAP σ). Мем на 2.5 σ выше среднего — +2.25: как настроение + вид события.
SEM_FLOOR = 1.0
SEM_WEIGHT = 1.5
SEM_CAP = 3.0
KIND_TAGS = {
    "kill": ("победа", "хвастовство", "шок", "уважение", "вау"),
    "knock": ("шок", "победа", "вау", "хвастовство"),
    "squad_wipe": ("победа", "хвастовство", "уважение", "вау", "шок"),
    "win": ("победа", "радость", "хвастовство", "уважение"),
    "death": ("фейл", "грусть", "злость", "кринж"),
    "teammate_fail": ("тиммейт", "фейл", "кринж", "непонимание"),
    "reaction": ("смех", "шок", "непонимание", "кринж", "радость"),
    "none": ("непонимание", "смех", "кринж"),
}
# Настроение реплики → теги мема (по основам слов): «смысл» без ИИ. Порядок тегов — от главного.
LINE_MOODS = (
    (("не надо", "нет нет", "стой", "стоп", "мама", "летим", "падаю", "упал", "аааа", "страшн", "испуг"),
     ("страх", "шок", "фейл")),
    (("убил", "убило", "умер", "сдох", "кончил", "лег", "лёг", "сбили", "меня нок", "всё", "конец", "анлак", "обидно"),
     ("фейл", "грусть", "кринж")),
    (("легко", "изи", "красав", "хорош", "нокнул", "убрал", "всех", "сквад", "чемпион", "умный", "сделал"),
     ("хвастовство", "победа", "радость")),
    (("почему", "зачем", "что это", "как так", "кажется", "не понял", "альцгейм", "забыл", "логично"),
     ("непонимание", "кринж")),
    (("ахах", "хаха", "смешн", "ржу", "угар"), ("смех", "радость")),
    (("тиммейт", "командн", "помоги", "где вы", "вы где", "один остал"), ("тиммейт", "непонимание", "фейл")),
)
# Слова, которые не делают «эхо» (служебные, местоимения, связки)
_ECHO_STOP = frozenset("""это эта этот эти того тому там тут так вот ну да нет ага угу я ты он она оно мы вы они меня тебя
его её ее нас вас их мне тебе ему ей нам вам им все всё всех уже еще ещё как что кто где когда зачем почему если чтобы
только просто вообще сейчас тоже очень было была были будет есть нету надо можно нужно давай давайте блин бля
потому поэтому другой другая другого какой какая такой такая одного одной один одна этого этой тогда потом снова
опять может вроде типа короче слушай смотри""".split())
_WORD = re.compile(r"[a-zа-яё0-9]+")


def _tokens(text: Any) -> list[str]:
    return [w.replace("ё", "е") for w in _WORD.findall(str(text or "").lower())]


def _content(text: Any) -> list[str]:
    """Значимые слова: ≥ 4 букв, не служебные и не мат (мат — не смысл, его глушат)."""
    from app.profanity import classify_token

    return [w for w in _tokens(text) if len(w) >= 4 and w not in _ECHO_STOP and not classify_token(w)]


def line_moods(text: str) -> list[str]:
    """Теги мемов под настроение реплики (LINE_MOODS), без повторов, по порядку важности; вопрос — «непонимание»."""
    low = " " + " ".join(_tokens(text)) + " "
    out: list[str] = []
    for stems, tags in LINE_MOODS:
        if any((" " + st) in low for st in stems):
            out += [t for t in tags if t not in out]
    if "?" in str(text) and "непонимание" not in out:
        out.append("непонимание")
    return out


def echo_words(line_text: str, meme: dict) -> list[str]:
    """Слова реплики, которые повторяет мем (по основе 5 букв, значимые слова)."""
    said = {w[:5] for w in _content(meme.get("transcript")) + _content(meme.get("title"))}
    return sorted({w for w in _content(line_text) if w[:5] in said})


@dataclass
class Candidate:
    fight: Fight
    t: float                    # куда мем (секунда исходника): сразу после сетапа
    kind: str                   # событие-удар (или reaction / none)
    text: str                   # реплика-сетап
    weight: float
    line_start: float | None = None
    last: bool = False


@dataclass
class Pick:
    cand: Candidate
    meme: dict
    fit: float
    echo: list[str] = field(default_factory=list)

    def brief(self) -> dict:
        return {"fight": self.cand.fight.key, "t": round(self.cand.t, 2), "kind": self.cand.kind,
                "line": self.cand.text[:80], "meme_id": self.meme["meme_id"],
                "meme": self.meme.get("title") or "", "fit": round(self.fit, 2), "echo": self.echo}


def candidates(fight: Fight) -> list[Candidate]:
    """Места под мем в бою: конец каждой реплики игрока (удар — решающее событие за 4 с до неё … 1 с после) и
    конец боя (последний кадр боя — мем; без реплики — ответ на итог боя)."""
    out: list[Candidate] = []
    dec = [e for e in fight.events if e["kind"] in WEIGHT and e["kind"] != "reaction"]
    for i, ln in enumerate(fight.lines):
        near = [e for e in dec if ln["start"] - 4.0 <= float(e["t"]) <= ln["end"] + 1.0]
        hot = any(e["kind"] == "reaction" and ln["start"] - 0.5 <= float(e["t"]) <= ln["end"] for e in fight.events)
        if near:
            kind = max(near, key=lambda e: WEIGHT.get(e["kind"], 0))["kind"]
            weight = 0.6 + WEIGHT.get(kind, 0.3) / 2
        else:
            kind, weight = "reaction", 0.45
        if hot or "!" in ln["text"] or "?" in ln["text"]:
            weight += 0.25
        last = i == len(fight.lines) - 1 and ln["end"] >= fight.end - 6.0
        if last:
            weight += 0.8
        out.append(Candidate(fight, round(ln["end"] + 0.05, 3), kind, ln["text"], round(weight, 3), ln["start"], last))
    if not any(c.last for c in out):
        kind = {"death": "death", "win": "win", "wipe": "squad_wipe", "kill": "kill"}.get(fight.result, "none")
        out.append(Candidate(fight, round(fight.end, 3), kind, "", 0.7, None, True))
    return out


def semantic_z(cand: Candidate, bank: Any) -> Any:
    """Смысл реплики-сетапа против каждого мема библиотеки (app/meme_semantic.line_scores — фраза и смысловое эхо
    слов): z-оценки по порядку библиотеки ``bank`` или None — модели нет / реплики нет. Реплики перед сетапом и вид
    события в запрос не идут: на выборе монтажёра они только мешали (tools/meme_semantic_eval.py)."""
    from app import meme_semantic as MS

    return MS.line_scores(cand.text, bank) if cand.text and bank is not None else None


def semantic_points(z: float | None) -> float:
    """Очки смысла: мем ближе среднего на SEM_FLOOR σ и больше — SEM_WEIGHT за каждую σ сверх, до SEM_CAP σ."""
    if z is None:
        return 0.0
    return SEM_WEIGHT * min(SEM_CAP, max(0.0, float(z) - SEM_FLOOR))


def meme_fit(meme: dict, cand: Candidate, semantic: float | None = None) -> tuple[float, list[str]]:
    """Насколько мем отвечает на момент: смысл реплики (``semantic`` — z-оценка из ``semantic_z``; None — модели
    нет), слова реплики в полях мема (app/video_memes.score), «эхо» — сильнее всего, настроение реплики и теги вида
    события — подсказка. Ответ — (очки, слова-эхо)."""
    from app.video_memes import score

    echo = echo_words(cand.text, meme) if cand.text else []
    query = " ".join(_content(cand.text))  # длинная реплика из служебных слов не топит смысл (оценка — доля слов)
    points = score(meme, query) if query else 0.0
    points += ECHO_POINTS * min(2, len(echo))
    points += semantic_points(semantic)
    tags = [str(t).lower() for t in meme.get("tags") or []]

    def has(tag: str) -> bool:
        return any(t == tag or t.startswith(tag[:5]) for t in tags)

    moods = line_moods(cand.text) if cand.text else []
    for rank, tag in enumerate(moods[:4]):  # настроение реплики — главнее вида события
        if has(tag):
            points += 2.2 if rank == 0 else 1.5
            break
    want = KIND_TAGS.get(cand.kind, ())
    for rank, tag in enumerate(want):
        if any(t == tag or t.startswith(tag[:5]) for t in tags):
            points += 1.6 if rank == 0 else 1.0
            break
    return round(points, 3), echo


def assign_memes(fights: list[Fight], library: list[dict], total_sec: float,
                 every_sec: float = MEME_EVERY_SEC) -> tuple[list[Pick], list[str]]:
    """Мемы на весь ролик: бюджет ~1 на ``every_sec`` с, не больше MAX_PER_FIGHT на бой, мемы одного боя не ближе
    MIN_GAP_SEC, ни одного повтора; лучшие пары (момент, мем) по ``вес момента × смысл`` первыми. Мем хуже MIN_FIT —
    момент без мема. Ответ — (выбор по времени ролика, заметки)."""
    notes: list[str] = []
    if not library:
        return [], ["одобренных видеомемов нет — эдит без мемов (одобрить: «Общие файлы → Видеомемы»)"]
    from app import meme_semantic as MS

    budget = max(1, int(round(total_sec / every_sec)))
    bank = MS.bank(library)  # None — модели нет: подбор по словам и тегам, как раньше
    if bank is None:
        notes.append("смысловой подбор мемов недоступен (модель не загрузилась) — мемы по словам и тегам")
    pairs: list[tuple[float, float, Candidate, dict, list[str]]] = []
    for f in fights:
        for cand in candidates(f):
            z = semantic_z(cand, bank)
            for i, meme in enumerate(library):
                fit, echo = meme_fit(meme, cand, None if z is None else float(z[i]))
                if fit >= (MIN_FIT_LAST if cand.last else MIN_FIT):
                    pairs.append((cand.weight * fit, fit, cand, meme, echo))
    pairs.sort(key=lambda p: (-p[0], -p[1], int(p[3]["meme_id"])))
    used_memes: set[int] = set()
    used_cands: set[int] = set()
    per_fight: dict[str, list[float]] = {}
    picks: list[Pick] = []
    for _value, fit, cand, meme, echo in pairs:
        if len(picks) >= budget:
            break
        mid = int(meme["meme_id"])
        if mid in used_memes or id(cand) in used_cands:
            continue
        times = per_fight.setdefault(cand.fight.key, [])
        if len(times) >= MAX_PER_FIGHT or any(abs(cand.t - t) < MIN_GAP_SEC for t in times):
            continue
        picks.append(Pick(cand, meme, fit, echo))
        used_memes.add(mid)
        used_cands.add(id(cand))
        times.append(cand.t)
    order = {f.key: i for i, f in enumerate(fights)}
    picks.sort(key=lambda p: (order[p.cand.fight.key], p.cand.t))
    if len(picks) < budget:
        notes.append(f"мемов по смыслу нашлось {len(picks)} из ~{budget} (1 на {every_sec:.0f} с): мало одобренных "
                     "мемов под эти реплики — добавьте (search_video_memes / «Общие файлы → Видеомемы»)")
    return picks, notes
