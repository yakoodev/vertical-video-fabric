"""Слой текста песни для мувика: выровненные строки (app/lyrics_align.py) → текстовые слои клипа (чистая функция).

Правила владельца (плейбук frag-movie, журнал монтажа 15.11) — «аналог субтитров»:

* ВСЕ спетые строки целиком и все повторы, по времени вокала; сомнительно распознанные (``unsure``) — нет;
* только в паузах — никогда во время стрельбы (``busy``: очереди выстрелов game_events, в секундах ролика) и не
  поверх реплики тиммейта (``avoid``);
* строка уходит за 3 кадра до перехода/склейки (``cuts``); не успевает пропеться до склейки — не ставится (сдвинь
  склейку snap_to_beats или кусок);
* крупно и стильно: жирные узкие капсы (Oswald 700), смешанный масштаб — короткий кусок строки огромным, слова
  загораются, когда поют (вход ``words`` на время пения куска); не на прицеле — по умолчанию нижняя половина кадра
  (``zone``), поля 6 %.

Слоёв — по куску строки (строка рвётся на куски на паузах вокала > 0.45 с и по 4 слова): на трёхминутную песню
это 60–100 слоёв из 200 возможных. Время — секунды ГОТОВОГО ролика; на шкалу слоёв (clip) переводит инструмент.
"""

from __future__ import annotations

from typing import Any, Iterable

LAYER_PREFIX = "lyr_"
LEAD_SEC = 0.04  # кусок появляется чуть раньше первого слога
HOLD_SEC = 0.45  # строка держится после последнего слова
NEXT_GAP_SEC = 0.06  # до следующей строки
CUT_FRAMES = 3  # уходит за 3 кадра до перехода
MIN_SHOW_SEC = 0.4
BUSY_MARGIN_SEC = 0.12
CHUNK_PAUSE_SEC = 0.45
CHUNK_MAX_WORDS = 4
MARGIN = 0.06
CHAR_EM = 0.5  # средняя ширина буквы капсом Oswald Bold, доля кегля
MAX_SIZE = 260.0
MIN_SIZE = 56.0
ZONES = ("bottom", "top", "center", "left", "right")
DEFAULT_STYLE = {"font": "Oswald", "weight": 700, "color": "#ffffff", "accent": "#ffd400", "uppercase": True,
                 "letterSpacing": 2, "stroke": {"color": "#000000", "width": 6}}


def _spans(raw: Iterable[Any] | None) -> list[tuple[float, float]]:
    out = []
    for item in raw or []:
        a, b = (item.get("start"), item.get("end")) if isinstance(item, dict) else (item[0], item[1])
        if a is not None and b is not None and float(b) > float(a):
            out.append((float(a), float(b)))
    return sorted(out)


def _chunks(words: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for word in words:
        cur = out[-1] if out else None
        if cur is None or len(cur) >= CHUNK_MAX_WORDS or float(word["t0"]) - float(cur[-1]["t1"]) > CHUNK_PAUSE_SEC:
            out.append([word])
        else:
            cur.append(word)
    return out


def _zone_box(zone: str, ref: tuple[float, float]) -> tuple[float, float, float, float]:
    """(x центра, y центра, ширина под текст в px, высота под текст в px) зоны в долях/px опорного кадра."""
    w, h = ref
    vertical = h > w
    if zone == "top":
        return 0.5, 0.27 if vertical else 0.24, w * (1 - 2 * MARGIN), h * (0.3 if vertical else 0.32)
    if zone == "center":
        return 0.5, 0.5, w * (1 - 2 * MARGIN), h * 0.36
    if zone in ("left", "right"):
        return (0.27 if zone == "left" else 0.73), 0.5, w * (0.46 - MARGIN), h * (1 - 2 * MARGIN) * 0.7
    return 0.5, 0.7 if vertical else 0.74, w * (1 - 2 * MARGIN), h * (0.28 if vertical else 0.3)


def _size(text: str, width_px: float, max_size: float) -> float:
    chars = max(1, len(text))
    return round(max(MIN_SIZE, min(max_size, width_px / (CHAR_EM * chars))), 1)


def build_lyric_layers(lines: list[dict], *, song_offset: float = 0.0, duration: float, fps: float = 30.0,
                       busy: Any = None, avoid: Any = None, cuts: Iterable[float] = (), only: Iterable[int] | None = None,
                       zone: str = "", ref: tuple[float, float] = (1080.0, 1920.0), style: dict | None = None,
                       track: int = 6, show_unsure: bool = False) -> dict:
    """Строки (секунды трека) → ``{layers, placed, skipped}``; время слоёв — секунды готового ролика
    (``start``/``end``), ролик = трек − ``song_offset``. ``only`` — номера строк (i), остальные не трогаются."""
    zone = zone if zone in ZONES else ("bottom" if ref[1] > ref[0] else "left")
    look = {**DEFAULT_STYLE, **{k: v for k, v in (style or {}).items() if v is not None}}
    max_size = float(look.pop("size", 0) or MAX_SIZE * ref[0] / 1080.0 if ref[1] > ref[0] else MAX_SIZE)
    accent = look.pop("accent", None)
    busy_spans, avoid_spans = _spans(busy), _spans(avoid)
    cut_list = sorted(float(c) for c in cuts or [])
    wanted = {int(i) for i in only} if only is not None else None
    cx, cy, box_w, box_h = _zone_box(zone, ref)
    frame = CUT_FRAMES / max(1.0, float(fps))
    layers: list[dict] = []
    placed: list[dict] = []
    skipped: list[dict] = []
    shown = [line for line in lines if line.get("words") and (wanted is None or int(line["i"]) in wanted)]
    for n, line in enumerate(shown):
        def skip(why: str) -> None:
            skipped.append({"i": line["i"], "text": line["text"], "why": why})

        if line.get("unsure") and not show_unsure:
            skip("сомнительно распознана (unsure)")
            continue
        words = [{**w, "t0": float(w["t0"]) - song_offset, "t1": float(w["t1"]) - song_offset} for w in line["words"]]
        start = words[0]["t0"] - LEAD_SEC
        sung_end = max(w["t1"] for w in words)
        end = sung_end + HOLD_SEC
        if n + 1 < len(shown):
            end = min(end, float(shown[n + 1]["words"][0]["t0"]) - song_offset - NEXT_GAP_SEC)
        if start < 0 or sung_end > duration:
            skip("вне ролика (песня в этом месте не звучит)")
            continue
        cut = next((c for c in cut_list if c > start + 1e-3), None)
        if cut is not None and cut - frame < end:
            if cut - frame < words[-1]["t0"] + 0.15:
                skip(f"склейка на {cut:.2f} с посреди строки — сдвинь склейку (snap_to_beats) или кусок")
                continue
            end = cut - frame
        end = min(end, duration)
        if end - start < MIN_SHOW_SEC:
            skip("мелькнёт меньше 0.4 с")
            continue
        hit = next(((a, b) for a, b in busy_spans if a - BUSY_MARGIN_SEC < end and b + BUSY_MARGIN_SEC > start), None)
        if hit:
            skip(f"стрельба {hit[0]:.1f}–{hit[1]:.1f} с — текст только в паузах после киллов")
            continue
        hit = next(((a, b) for a, b in avoid_spans if a < end and b > start), None)
        if hit:
            skip(f"реплика {hit[0]:.1f}–{hit[1]:.1f} с — строку поверх голоса не ставим")
            continue
        chunks = _chunks(words)
        texts = [" ".join(str(w["w"]) for w in c) for c in chunks]
        sizes = [_size(t, box_w, max_size) for t in texts]
        total_h = sum(sizes) * 1.02
        if total_h > box_h:  # строка из многих кусков — мельче, чтобы влезть в зону
            k = box_h / total_h
            sizes = [round(max(MIN_SIZE, s * k), 1) for s in sizes]
            total_h = sum(sizes) * 1.02
        top = cy * ref[1] - total_h / 2
        key = max(range(len(chunks)), key=lambda j: sizes[j]) if len(chunks) > 1 else -1
        y_px = top
        for j, (chunk, text, size) in enumerate(zip(chunks, texts, sizes)):
            c_start = max(start, chunk[0]["t0"] - LEAD_SEC)
            sung = max(0.12, min(3.0, chunk[-1]["t0"] - chunk[0]["t0"] + 0.12))
            props = {**look, "text": text, "size": size, "maxWidth": round(box_w), "align": "center"}
            if j == key and accent:
                props["color"] = accent
            layers.append({
                "id": f"{LAYER_PREFIX}{line['i']}_{j}", "type": "text", "name": f"текст песни {line['i']}",
                "track": int(track), "start": round(c_start, 3), "end": round(end, 3),
                "x": cx, "y": round((y_px + size * 0.51) / ref[1], 4),
                "in": {"type": "words" if len(chunk) > 1 else "pop", "duration": round(min(sung, end - c_start), 3)},
                "out": {"type": "fade", "duration": 0.08}, "props": props})
            y_px += size * 1.02
        placed.append({"i": line["i"], "text": line["text"], "start": round(start, 3), "end": round(end, 3),
                       "chunks": len(chunks), "lines": texts,
                       "words": [{"w": str(w["w"]), "t": round(float(w["t0"]), 3)} for w in words],
                       "layer_ids": [f"{LAYER_PREFIX}{line['i']}_{j}" for j in range(len(chunks))]})
    return {"layers": layers, "placed": placed, "skipped": skipped, "zone": zone}


def tracked_lyric_layer(placed: dict, track_id: str, *, start: float, end: float, word_times: list[float],
                        style: dict | None = None, track: int = 6) -> dict:
    """Строка текста песни словом «в мире игры» (слой ``tracked_text`` на поверхности по треку ``track_id``):
    куски строки — строки текста, каждое слово появляется в свой слог (``word_times`` — шкала clip, по словам)."""
    props: dict = {"text": "\n".join(placed.get("lines") or [placed["text"]]).upper(), "track": track_id,
                   "words": [{"t": round(float(t), 3)} for t in word_times][:40], "reveal": "pop"}
    for key, value in (style or {}).items():
        if key in ("preset", "font", "color", "gradient", "stroke", "extrude", "skew", "fit") and value is not None:
            props[key] = value
    return {"id": f"{LAYER_PREFIX}{placed['i']}_w", "type": "tracked_text", "name": f"текст песни {placed['i']} (в мире)",
            "track": int(track), "start": round(start, 3), "end": round(end, 3),
            "out": {"type": "fade", "duration": 0.08}, "props": props}


__all__ = ["LAYER_PREFIX", "ZONES", "build_lyric_layers", "tracked_lyric_layer"]
