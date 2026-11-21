"""Сигналы моментов сверх чата: немая реакция + клипы зрителей Twitch + тепловая карта и метки автосубтитров YouTube.

Все — 10-секундные корзины секунд исходника с кривой 0..1 (как ``chat_signal.build_signal``). Хранятся в кеше
``runtime/moment_signals/extras_<id>.json``; ``chat_signal.rerank`` читает только кеш (быстро, без сети и звука),
пересчёт — ``refresh_source_signals`` (автопилот после анализа, MCP ``moment_signals``).

Вес каждого — мягкий (``WEIGHTS``), как у чата: «только как дополнение». Нет ни одного сигнала — порядок и
оценка как были.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

WEIGHTS = {"reaction": 0.12, "viewer_clips": 0.10, "yt_heatmap": 0.10, "yt_tags": 0.06}
VERSION = 1


def _path(source_id: int) -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "moment_signals"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"extras_{int(source_id)}.json"


def load_extras(source_id: int) -> dict[str, dict]:
    """Сохранённые внешние сигналы источника: {имя: {bucket, curve, ...}}; нет — {}."""
    path = _path(source_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (data.get("signals") or {}).items() if isinstance(v, dict) and v.get("curve")}


def save_extras(source_id: int, signals: dict[str, dict | None], notes: list[str] | None = None) -> None:
    clean = {k: v for k, v in signals.items() if v}
    _path(source_id).write_text(json.dumps({"v": VERSION, "signals": clean, "notes": notes or []}, ensure_ascii=False),
                                encoding="utf-8")


def source_extras(store: Any, source_id: int) -> dict[str, dict]:
    """Кеш внешних сигналов + кеш немых реакций (если посчитаны)."""
    out = load_extras(source_id)
    if "reaction" not in out:
        try:
            from app.silent_reaction import cached_reactions

            hit = cached_reactions(store, source_id)
        except Exception:  # noqa: BLE001 - дополнение: нет данных — нет сигнала
            hit = None
        if hit and hit.get("signal"):
            out["reaction"] = hit["signal"]
    return out


def range_value(signal: dict | None, start: float, end: float) -> float | None:
    """Максимум кривой в корзинах отрезка [start, end] (без задержки — сигналы стоят на самом моменте)."""
    if not signal or end <= start:
        return None
    size = float(signal["bucket"])
    window = signal["curve"][max(0, int(start // size)): int(end // size) + 1]
    return max(window) if window else 0.0


def plan_extras(extras: dict[str, dict], plan: dict) -> dict[str, float]:
    """{сигнал: 0..1} для кусков клипа (лучший кусок)."""
    segs = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []
            if float(s.get("end_sec") or 0) > float(s.get("start_sec") or 0)]
    out: dict[str, float] = {}
    for name, signal in extras.items():
        values = [v for v in (range_value(signal, a, b) for a, b in segs) if v is not None]
        if values:
            out[name] = max(values)
    return out


def blend_extras(quality: float, values: dict[str, float], weights: dict[str, float] = WEIGHTS) -> float:
    """Мягкий вес каждого сигнала, как ``chat_signal.blend``: без сигналов — оценка как была."""
    q = float(quality)
    for name, value in values.items():
        w = weights.get(name, 0.0)
        if w > 0:
            q = (1 - w) * q + w * max(0.0, min(1.0, float(value)))
    return round(q, 4)


def refresh_source_signals(store: Any, source_id: int, *, reactions: bool = True, network: bool = True,
                           gql: Any = None, ytdlp: Any = None, subs_runner: Any = None) -> dict:
    """Пересчитать сигналы источника: немая реакция (звук+транскрипт), клипы зрителей (Twitch VOD), тепловая
    карта и метки автосубтитров (YouTube). Каждый — по возможности: что не вышло — в ``notes``."""
    from app import external_signals as ext

    source = store.get_source(int(source_id))
    url = str(source.get("original_url") or "")
    duration = float(source.get("duration_sec") or 0) or None
    signals: dict[str, dict | None] = dict(load_extras(source_id))
    notes: list[str] = []
    if reactions:
        try:
            from app.silent_reaction import compute_source_reactions

            data = compute_source_reactions(store, source_id)
            signals["reaction"] = data.get("signal")
            notes.append(f"немых реакций: {len(data.get('reactions') or [])}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"немая реакция: {exc}")
    vod = ext.twitch_vod_id(url) or str((source.get("metadata") or {}).get("twitch_vod_id") or "") or None
    if network and vod:
        try:
            clips = ext.fetch_vod_clips(vod, gql=gql or ext._gql_post)
            signals["viewer_clips"] = ext.clips_signal(clips, duration)
            notes.append(f"клипов зрителей с VOD: {len(clips)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"клипы Twitch: {exc}")
    if network and ext.is_youtube(url):
        try:
            info = ext.ytdlp_info(url, runner=ytdlp)
            signals["yt_heatmap"] = ext.heatmap_signal(info.get("heatmap"), duration)
            notes.append("тепловая карта: " + ("есть" if signals["yt_heatmap"] else "нет (мало просмотров)"))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"тепловая карта: {exc}")
        try:
            with tempfile.TemporaryDirectory() as td:
                vtt = ext.fetch_auto_subs(url, td, runner=subs_runner)
            tags = ext.parse_vtt_tags(vtt)
            signals["yt_tags"] = ext.tags_signal(tags, duration)
            notes.append(f"меток смеха/крика в автосубтитрах: {len(tags)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"автосубтитры: {exc}")
    save_extras(source_id, signals, notes)
    return {"source_id": int(source_id), "signals": sorted(k for k, v in signals.items() if v), "notes": notes}


def agreement(a: dict | None, b: dict | None, top: int = 10) -> float | None:
    """Совпадение пиков двух сигналов (доля top-корзин ``a``, рядом с которыми ±1 корзина есть top ``b``).
    Замер «клипы зрителей ↔ пики чата»: число 0..1, нет данных — None."""
    if not a or not b:
        return None

    def tops(sig: dict) -> list[int]:
        curve = sig["curve"]
        order = sorted((i for i in range(len(curve)) if curve[i] > 0), key=lambda i: -curve[i])
        return order[:top]

    ta, tb = tops(a), set(tops(b))
    if not ta or not tb:
        return None
    return round(sum(1 for i in ta if {i - 1, i, i + 1} & tb) / len(ta), 3)


PEAKS_NOTE = ("moment_peaks — сигналы моментов (дополнение, не приказ): reaction — немая реакция (плотная речь, затем "
              "3–8 с без слов, но громко), viewer_clips — зрители Twitch сделали клип с этого места, yt_heatmap — "
              "пересматривают на YouTube, yt_tags — [смех]/[крик] в автосубтитрах YouTube. t — начало 10-секундной корзины")


def window_peaks(store: Any, source_id: int, lo: float | None, hi: float | None, to_out: Any = None,
                 top: int = 8, min_value: float = 0.5) -> list[dict]:
    """Сильные корзины сигналов источника в окне [lo, hi] (секунды исходника или через ``to_out`` — шкала ответа)."""
    out: list[dict] = []
    for name, signal in source_extras(store, int(source_id)).items():
        size = float(signal["bucket"])
        for i, v in enumerate(signal["curve"]):
            t = i * size
            if v < min_value or (lo is not None and t + size < lo) or (hi is not None and t > hi):
                continue
            mapped = t if to_out is None else to_out(max(t, lo or 0.0))
            if mapped is not None:
                out.append({"t": round(float(mapped), 2), "signal": name, "value": round(float(v), 2)})
    return sorted(sorted(out, key=lambda p: -p["value"])[:top], key=lambda p: p["t"])
