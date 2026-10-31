"""Чат Twitch как дополнительный сигнал моментов.

Владелец: «только как дополнение. большую ставку на это не делаем». Поэтому чат здесь —
мягкий вес (``CHAT_WEIGHT``) поверх обычной оценки кандидата, а не отбор сам по себе. Нет
импортированного чата у источника — все функции молча возвращают «нет данных», и отбор
работает как раньше.

Всплеск по 10-секундным корзинам секунд **исходника**: скорость сообщений против местной
медианы (±5 мин), доля смеха (KEKW, LUL, «ахах»…), донаты/читы/подписки. Один зритель в
корзине считается не больше ``PER_USER`` раз — спам одного человека не делает момент.
Зрители пишут через 5–20 с после события, поэтому окно кандидата [start, end] смотрит чат
в [start + 5, end + 20], а пик чата для агента сдвинут на ~10 с раньше (``moment_t``).
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable

BUCKET = 10.0
LAG = (5.0, 20.0)
CHAT_WEIGHT = 0.15
PER_USER = 3
MIN_PEAK = 0.35

LAUGH_TOKENS = {
    "kekw", "lul", "lulw", "omegalul", "kekl", "icant", "pepelaugh", "lmao", "lmfao", "lol", "xd", "kek",
    "ору", "орууу", "лол", "кек", "ржу", "ржака", "😂", "🤣",
}
_LAUGH_RE = re.compile(r"^(?:[аa]?[хx][аa]){2,}|^(?:[аa][хx]){2,}|^(?:ha){2,}|^(?:хе){2,}|^(?:ор)у{2,}", re.I)
DONATION_USERS = {"donationalerts", "donationalerts_bot", "streamelements", "streamlabs", "donatepay", "donatty",
                  "da_bot", "memealerts"}
_DONATION_RE = re.compile(
    r"\bcheer\d+\b|\b\d+\s?(?:₽|руб\w*|rub|р\.|\$|usd|eur|€|bits?)(?=\W|$)|задонат|донат(?:ит|ил|ик)?\b|"
    r"donated|\bsubscribed\b|gifted|подписал(?:ся|ась)|подарил\w* подписк", re.I)

_cache: dict[tuple, dict | None] = {}
_lock = threading.Lock()


def is_laugh(text: str) -> bool:
    return any(w.lower().strip(".,!?") in LAUGH_TOKENS or _LAUGH_RE.match(w) for w in (text or "").split())


def is_donation(user: str, text: str) -> bool:
    return (user or "").lower() in DONATION_USERS or bool(_DONATION_RE.search(text or ""))


def _median(values: list[float]) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def build_signal(messages: Iterable[tuple[float, str, str]], bucket: float = BUCKET) -> dict | None:
    """(секунда, логин, текст) → корзины {count, laugh, donations} и кривая всплеска ``curve`` 0..1."""
    counts: dict[int, int] = {}
    laughs: dict[int, int] = {}
    donations: dict[int, int] = {}
    per_user: dict[tuple[int, str], int] = {}
    for offset, user, text in messages:
        idx = int(max(0.0, float(offset)) // bucket)
        key = (idx, (user or "").lower())
        per_user[key] = per_user.get(key, 0) + 1
        if per_user[key] > PER_USER:
            continue
        counts[idx] = counts.get(idx, 0) + 1
        if is_laugh(text):
            laughs[idx] = laughs.get(idx, 0) + 1
        if is_donation(user, text):
            donations[idx] = donations.get(idx, 0) + 1
    if not counts:
        return None
    n = max(counts) + 1
    series = [counts.get(i, 0) for i in range(n)]
    global_median = max(1.0, _median([c for c in series if c]))
    half = max(3, int(round(300 / bucket)))
    raw = []
    for i, count in enumerate(series):
        if not count:
            raw.append(0.0)
            continue
        baseline = max(1.0, _median(series[max(0, i - half): i + half + 1]), global_median * 0.5)
        damp = count / (count + global_median)  # «всплеск» из двух сообщений в тихом чате гасим
        laugh = laughs.get(i, 0) / count
        raw.append((count / baseline) * (1 + 1.5 * laugh) * damp + 0.5 * min(3, donations.get(i, 0)))
    peak = max(raw) or 1.0
    return {
        "bucket": bucket,
        "count": series,
        "laugh": [laughs.get(i, 0) for i in range(n)],
        "donations": [donations.get(i, 0) for i in range(n)],
        "curve": [round(v / peak, 4) for v in raw],
    }


def source_signal(store: Any, source_id: int) -> dict | None:
    """Сигнал чата источника (кэш до смены числа сообщений). Нет чата — None."""
    try:
        from app.twitch_chat import _db

        db = _db(store)
        row = db.query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (int(source_id),))
        total = int(row["n"]) if row else 0
    except Exception:  # noqa: BLE001 - чат — дополнение: нет таблицы/базы — нет сигнала
        return None
    if not total:
        return None
    key = (str(getattr(db, "path", id(db))), int(source_id), total)
    with _lock:
        if key in _cache:
            return _cache[key]
    rows = db.query_all("SELECT offset_sec, user_login, user_name, text FROM chat_messages WHERE source_id = ? "
                        "ORDER BY offset_sec", (int(source_id),))
    signal = build_signal((r["offset_sec"], r["user_login"] or r["user_name"], r["text"]) for r in rows)
    with _lock:
        for stale in [k for k in _cache if k[:2] == key[:2]]:
            _cache.pop(stale, None)
        _cache[key] = signal
    return signal


def range_score(signal: dict | None, start: float, end: float) -> float | None:
    """Всплеск чата для отрезка исходника [start, end] (с задержкой зрителей), 0..1; без чата — None."""
    if not signal or end <= start:
        return None
    size = signal["bucket"]
    lo, hi = int((start + LAG[0]) // size), int((end + LAG[1]) // size)
    window = signal["curve"][max(0, lo): hi + 1]
    return max(window) if window else 0.0


def plan_score(signal: dict | None, plan: dict) -> float | None:
    segs = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []
            if float(s.get("end_sec") or 0) > float(s.get("start_sec") or 0)]
    scores = [range_score(signal, a, b) for a, b in segs]
    scores = [s for s in scores if s is not None]
    return max(scores) if scores else None


def blend(quality: float, chat: float | None, weight: float = CHAT_WEIGHT) -> float:
    """Мягкий вес: без чата — оценка как была."""
    if chat is None:
        return quality
    return round((1 - weight) * quality + weight * max(0.0, min(1.0, chat)), 4)


def rerank(store: Any, plans: list[dict], weight: float = CHAT_WEIGHT) -> list[dict]:
    """Порядок кандидатов (как выдал анализ) с мягкой поправкой на чат; без чата — тот же порядок."""
    signals: dict[int, dict | None] = {}
    for plan in plans:
        sid = int(plan.get("source_id") or 0)
        if sid and sid not in signals:
            signals[sid] = source_signal(store, sid)
    if not any(signals.values()):
        return list(plans)
    n = max(1, len(plans))

    def key(item: tuple[int, dict]) -> float:
        index, plan = item
        chat = plan_score(signals.get(int(plan.get("source_id") or 0)), plan)
        return -blend(1 - index / n, chat, weight)

    return [plan for _, plan in sorted(enumerate(plans), key=key)]


def peaks(signal: dict | None, lo: float | None = None, hi: float | None = None, top: int = 8,
          min_gap: float = 45.0) -> list[dict]:
    """Пики чата в окне исходника: ``moment_t`` — где вероятно само событие (на ~10 с раньше чата)."""
    if not signal:
        return []
    size = signal["bucket"]
    curve = signal["curve"]
    order = sorted(range(len(curve)), key=lambda i: (-curve[i], i))
    picked: list[int] = []
    for i in order:
        if len(picked) >= top or curve[i] < MIN_PEAK:
            break
        t = i * size
        if (lo is not None and t + size < lo) or (hi is not None and t > hi + LAG[1]):
            continue
        if all(abs(i - j) * size >= min_gap for j in picked):
            picked.append(i)
    out = []
    for i in sorted(picked):
        t = i * size
        out.append({"chat_t": round(t, 1), "moment_t": round(max(0.0, t - 10.0), 1), "score": curve[i],
                    "per_min": round(signal["count"][i] * 60 / size), "laugh": signal["laugh"][i],
                    "donations": signal["donations"][i]})
    return out


PEAKS_NOTE = ("chat_peaks — всплески чата Twitch (дополнение, не приказ): moment_t — где вероятно событие "
              "(зрители пишут через 5–20 с), per_min — сообщений в минуту, laugh — смех, donations — донаты. "
              "Бери момент, только если он сильный и на видео/в звуке")


def audio_map_peaks(store: Any, source_id: int, lo: float | None, hi: float | None,
                    to_out: Any = None) -> list[dict]:
    """Пики для карты моментов: секунды исходника (или через ``to_out`` — в шкалу ответа)."""
    try:
        found = peaks(source_signal(store, int(source_id)), lo, hi)
    except Exception:  # noqa: BLE001 - чат не ломает audio_map
        return []
    if to_out is None:
        return found
    out = []
    for p in found:
        t = to_out(p["moment_t"])
        if t is not None:
            out.append({**p, "moment_t": round(float(t), 2), "source_t": p["moment_t"]})
    return out


def clip_time_mapper(plan: dict) -> Any:
    """Секунда исходника → секунда клипа (None — вне кусков)."""
    from app.twitch_chat import clip_piece_starts

    pieces, starts = clip_piece_starts(plan)

    def to_clip(t: float) -> float | None:
        for (a, b), c in zip(pieces, starts):
            if a <= t < b:
                return c + t - a
        return None

    return to_clip
