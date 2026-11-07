"""Чат Twitch: активность и всплески (из ``app/twitch_chat.py``)."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Iterable

from app.twitch_chat_store import _db, chat_emotes, has_chat

# ------------------------------------------------------------------- активность


# Реакции, которые означают «тут что-то случилось»: смех, вау, плюсы/W.
HYPE_TOKENS = {
    t.lower()
    for t in (
        "KEKW LUL LULW OMEGALUL KEKL ICANT pepeLaugh LMAO LMFAO LOL xD XD KEK "
        "PogChamp Pog POGGERS PogU POGGIES PagMan PagChomp W WW WWW + ++ +++ "
        "GG EZ Clap monkaS monkaW D: WAYTOODANK Kreygasm OOOO ? ?? ??? !!! "
        "ЛОЛ КЕК ОРУ ЖЕСТЬ ВАУ ИМБА КРАСАВА БАЗА ГГ АХАХ ХАХА"
    ).split()
}
_LAUGH_RE = re.compile(r"^(?:[аa]?[хx][аa]){2,}|^(?:[аa][хx]){2,}|^(?:ha){2,}|^(?:хе){2,}|^\++$|^w+$", re.I)

_activity_cache: dict[tuple, dict] = {}
_activity_lock = threading.Lock()


def _hype_token(token: str) -> bool:
    low = token.lower()
    return low in HYPE_TOKENS or bool(_LAUGH_RE.match(low))


def _is_caps(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 4:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= 0.7


def score_activity(
    messages: Iterable[tuple[float, str]],
    bucket: float = 10.0,
    duration: float = 0.0,
    emote_names: Iterable[str] = (),
) -> dict:
    """Сообщения/бакет и нормированный «хайп» 0..1.

    hype ∝ (частота / локальная медиана ±5 мин) × (1 + 1.5·доля реакций +
    0.5·доля капса) × count/(count+медиана) — последний множитель гасит
    «всплеск» из двух сообщений в тихом чате.
    """
    bucket = max(1.0, float(bucket))
    emote_set = set(emote_names)
    counts: dict[int, int] = {}
    hype_msgs: dict[int, int] = {}
    caps_msgs: dict[int, int] = {}
    tokens: dict[int, Counter] = {}
    last = 0
    for offset, text in messages:
        idx = int(max(0.0, float(offset)) // bucket)
        last = max(last, idx)
        counts[idx] = counts.get(idx, 0) + 1
        words = (text or "").split()
        reacts = [w for w in words if w in emote_set or _hype_token(w)]
        if reacts:
            hype_msgs[idx] = hype_msgs.get(idx, 0) + 1
            tokens.setdefault(idx, Counter()).update(set(reacts))
        if _is_caps(text or ""):
            caps_msgs[idx] = caps_msgs.get(idx, 0) + 1
    n = max(last + 1, int(math.ceil(float(duration or 0) / bucket)) if duration else 0)
    if not counts:
        return {"bucket": bucket, "total": 0, "buckets": []}
    series = [counts.get(i, 0) for i in range(n)]
    global_median = max(1.0, _median([c for c in series if c > 0]))
    half = max(3, int(round(300 / bucket)))  # ±5 минут
    raw: list[float] = []
    for i, count in enumerate(series):
        if not count:
            raw.append(0.0)
            continue
        window = series[max(0, i - half) : i + half + 1]
        baseline = max(1.0, _median(window), global_median * 0.5)
        ratio = count / baseline
        react = hype_msgs.get(i, 0) / count
        caps = caps_msgs.get(i, 0) / count
        damp = count / (count + global_median)
        raw.append(ratio * (1 + 1.5 * react + 0.5 * caps) * damp)
    peak = max(raw) or 1.0
    buckets = []
    for i, count in enumerate(series):
        top, top_count = "", 0
        if i in tokens:
            top, top_count = tokens[i].most_common(1)[0]
        buckets.append(
            {
                "t": round(i * bucket, 3),
                "count": count,
                "hype": round(raw[i] / peak, 4),
                "reactions": hype_msgs.get(i, 0),
                "caps": caps_msgs.get(i, 0),
                "top": top,
                "top_count": top_count,
            }
        )
    return {"bucket": bucket, "total": sum(series), "buckets": buckets}


def _median(values: list[float]) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def chat_activity(store, source_id: int, bucket: float = 10.0) -> dict:
    """Активность чата источника (кэш в памяти до смены числа сообщений)."""
    db = _db(store)
    count_row = db.query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
    total = int(count_row["n"]) if count_row else 0
    duration = 0.0
    try:
        duration = float(store.get_source(source_id).get("duration_sec") or 0)
    except Exception:  # noqa: BLE001
        pass
    key = (str(db.path), source_id, float(bucket), total)
    with _activity_lock:
        cached = _activity_cache.get(key)
    if cached is not None:
        return cached
    emote_names = list(chat_emotes(store, source_id).keys())
    # Идём курсором, не fetchall: в 4-часовом VOD сотни тысяч сообщений. Своё соединение, без замка записи:
    # разбор идёт секунды, и всё это время база открыта для всех.
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT offset_sec, text FROM chat_messages WHERE source_id = ? ORDER BY offset_sec", (source_id,)
        )
        result = score_activity(((r[0], r[1]) for r in cur), bucket, duration, emote_names)
    finally:
        conn.close()
    result["source_id"] = source_id
    with _activity_lock:
        for stale in [k for k in _activity_cache if k[:2] == key[:2]]:
            _activity_cache.pop(stale, None)
        _activity_cache[key] = result
    return result


def hype_windows_from_activity(
    activity: dict, top: int = 20, min_gap: float = 45.0, min_hype: float = 0.2
) -> list[dict]:
    """Самые горячие окна: пики хайпа ≥ ``min_hype`` (не ближе ``min_gap`` друг
    к другу), расширенные на соседние бакеты не ниже половины пика (до ±60 с);
    окна не перекрываются — более горячее забирает бакеты первым."""
    buckets = activity.get("buckets") or []
    size = float(activity.get("bucket") or 10.0)
    order = sorted(range(len(buckets)), key=lambda i: (-buckets[i]["hype"], i))
    picked: list[int] = []
    for i in order:
        if len(picked) >= max(1, int(top)) or buckets[i]["hype"] < min_hype or not buckets[i]["count"]:
            break
        if all(abs(buckets[i]["t"] - buckets[j]["t"]) >= min_gap for j in picked):
            picked.append(i)
    reach = max(1, int(60 // size))
    claimed: set[int] = set(picked)
    windows = []
    for i in picked:
        level = buckets[i]["hype"] * 0.5
        lo = hi = i
        while lo - 1 >= 0 and i - (lo - 1) <= reach and lo - 1 not in claimed and buckets[lo - 1]["hype"] >= level:
            lo -= 1
        while (
            hi + 1 < len(buckets)
            and (hi + 1) - i <= reach
            and hi + 1 not in claimed
            and buckets[hi + 1]["hype"] >= level
        ):
            hi += 1
        claimed.update(range(lo, hi + 1))
        span = buckets[lo : hi + 1]
        tokens: Counter = Counter()
        for b in span:
            if b["top"]:
                tokens[b["top"]] += b["top_count"]
        top_token, top_count = tokens.most_common(1)[0] if tokens else ("", 0)
        start, end = buckets[lo]["t"], buckets[hi]["t"] + size
        label = f"{format_ts(start)}–{format_ts(end)} ({sum(b['count'] for b in span)} сообщ."
        label += f", {top_token} x{top_count})" if top_token else ")"
        windows.append(
            {
                "start": start,
                "end": end,
                "peak_t": buckets[i]["t"],
                "hype": buckets[i]["hype"],
                "count": sum(b["count"] for b in span),
                "top": top_token,
                "top_count": top_count,
                "label": label,
            }
        )
    return windows


def chat_hype_windows(store, source_id: int, top: int = 20, bucket: float = 10.0) -> list[dict]:
    """Самые горячие окна чата источника (по убыванию хайпа). Нет чата — []."""
    if not has_chat(store, source_id):
        return []
    return hype_windows_from_activity(chat_activity(store, source_id, bucket), top=top)


def chat_prompt_block(store, source_id: int, top: int = 15) -> str:
    """Короткий блок для промпта анализа; пусто, если чата нет или он выключен."""
    try:
        if not bool(store.get_app_setting_value("analysis_use_chat", True)):
            return ""
        windows = chat_hype_windows(store, source_id, top=top)
    except Exception:  # noqa: BLE001 - чат — подсказка, анализ без него не падает
        return ""
    if not windows:
        return ""
    lines = [
        "Чат зрителей (Twitch): всплески активности. Зрители реагируют с задержкой "
        "~5–20 с, поэтому само событие обычно чуть РАНЬШЕ всплеска. Это подсказка, "
        "а не приказ: бери момент, только если он и на видео сильный."
    ]
    for w in sorted(windows, key=lambda w: w["start"]):
        lines.append(f"- {w['label']}, хайп {int(round(w['hype'] * 100))}%")
    return "\n".join(lines)


def format_ts(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
