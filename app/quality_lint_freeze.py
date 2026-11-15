"""Линт качества: застывшие куски готового рендера (``freezes``, из ``app/quality_lint.py``).

Зачем (журнал монтажа 15.11, мувик BURN v3/v4): «клип просто остановился» — 2–3 с почти стоп-кадра
(запись 60 к/с, прочитанная как 120: двойная скорость и застывший конец; пауза под текст в почти-стоп-кадре).
QC рендера (``app/render_qc.py``, ffmpeg ``freezedetect``) ловит только стоп от 2 с, и его обманывают зерно
и титры поверх: шум и бегущий текст для него — «движение». Правило владельца — прогон на застывшие куски
от 0.6 с, «кадр к кадру без оверлеев».

Как меряем. Кадры готового файла — по ``FREEZE_FPS`` в секунду, сеткой ``FLASH_SIZE``×``FLASH_SIZE``
(усреднение ``area`` по клетке ~27×48 px у 1080×1920: зерно гасится, как размытием). Каждую клетку сравниваем
с опорным кадром начала отрезка (а не с соседним: медленный дрейф копится, слоу-мо почти до стопа — нет):
клетка «сдвинулась», если средняя разница больше ``FREEZE_CELL_DIFF`` (из 255). Клетки под оверлеями не
считаются: слои клипа (титры, субтитры, эмодзи, стикеры, плашки — ``layer_box``, субтитры — полосой), а
оверлеи без слоя (прожигаемые субтитры, текст, вшитый в запись) гасит порог — кадр стоит, если сдвинулось
не больше ``FREEZE_MOVING_SHARE`` остальных клеток. Ровная заливка (чёрный, затемнение) не судится — это
``blackdetect`` QC. Стоп-кадры клипа (``freeze_frame``) и стоп-кадр в конце (``set_end_hold``) — нарочно,
их пропускаем.

Только предупреждения, по последнему готовому рендеру; время находки — шкала clip (``t``, ``until``) и
готового ролика (``final_t``).
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app.quality_lint_base import _finding

FREEZE_FPS = 10  # кадров в секунду для сравнения: 0.6 с — 6 кадров
FREEZE_MIN_SEC = 0.6
FREEZE_CELL_DIFF = 3.0  # клетка сдвинулась: средняя разница с опорным кадром больше (яркость 0..255)
# Кадр «стоит», если сдвинулось не больше этой доли открытых клеток. Замер 16.11 на рендерах владельца: застывшие
# места мувика BURN v3 (2:20) и v4 (2:27–2:31) — 0–4 %, исправленный v4.1 — ни одной находки; говорящий в кадре и
# обычная игра — от 8–12 %.
FREEZE_MOVING_SHARE = 0.05
FREEZE_FLAT_STD = 4.0  # ровная заливка (чёрный, затемнение) — не судим
FREEZE_MIN_OPEN_SHARE = 0.3  # под оверлеями почти весь кадр — не судим
FREEZE_MASK_PAD = 1  # клеток запаса вокруг слоя (обводка, тень, анимация масштаба)
FREEZE_MERGE_GAP = 1.0  # застывшие отрезки с промежутком меньше — одна находка (v4: три куска одного коридора)
CAPTION_BAND = 0.09  # полувысота полосы субтитров (доля кадра)
MAX_FREEZE_FINDINGS = 10


def gray_cells(frames: Any) -> Any:
    """Кадры ``(n, S, S, 3)`` uint8 → яркость клеток ``(n, S, S)`` float32 (0..255, BT.601)."""
    import numpy as np

    rgb = np.asarray(frames, dtype=np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


class FreezeScanner:
    """Поток кадров-сеток → застывшие отрезки (секунды файла).

    ``mask_at(t)`` — клетки под оверлеями в момент ``t`` (bool ``S×S``, True — не считать) или None;
    ``skip_at(t)`` — True, если кадр нарочно стоит (стоп-кадр клипа, стоп-кадр в конце): там отрезок
    обрывается и не судится."""

    def __init__(self, fps: float = FREEZE_FPS, *, mask_at: Callable[[float], Any] | None = None,
                 skip_at: Callable[[float], bool] | None = None, min_sec: float = FREEZE_MIN_SEC,
                 cell_diff: float = FREEZE_CELL_DIFF, moving_share: float = FREEZE_MOVING_SHARE):
        self.fps = float(fps)
        self.mask_at, self.skip_at = mask_at, skip_at
        self.min_sec, self.cell_diff, self.moving_share = float(min_sec), float(cell_diff), float(moving_share)
        self.index = 0
        self.runs: list[dict] = []
        self._anchor: Any = None
        self._anchor_mask: Any = None
        self._start = self._last = 0.0
        self._peak = 0.0
        self._masked = 0.0

    def _close(self) -> None:
        if self._anchor is not None:
            length = self._last - self._start + 1.0 / self.fps
            if self._last > self._start and length >= self.min_sec - 1e-6:
                self.runs.append({"start": round(self._start, 2), "end": round(self._last + 1.0 / self.fps, 2),
                                  "duration": round(length, 2), "moving_share": round(self._peak, 3),
                                  "masked_share": round(self._masked, 3)})
        self._anchor = self._anchor_mask = None

    def _open(self, frame: Any, mask: Any, t: float) -> None:
        self._anchor, self._anchor_mask = frame, mask
        self._start = self._last = t
        self._peak = 0.0
        self._masked = float(mask.mean()) if mask is not None else 0.0

    def feed(self, gray: Any) -> None:
        import numpy as np

        for frame in np.asarray(gray, dtype=np.float32):
            t = self.index / self.fps
            self.index += 1
            if self.skip_at is not None and self.skip_at(t):
                self._close()
                continue
            mask = self.mask_at(t) if self.mask_at is not None else None
            if mask is not None and not mask.any():
                mask = None
            if self._anchor is None:
                if self._judgeable(frame, mask):
                    self._open(frame, mask, t)
                continue
            both = mask if self._anchor_mask is None else (
                self._anchor_mask if mask is None else (mask | self._anchor_mask))
            if not self._judgeable(frame, both):
                self._close()
                continue
            moved = np.abs(frame - self._anchor) > self.cell_diff
            share = float(moved[~both].mean()) if both is not None else float(moved.mean())
            if share <= self.moving_share:
                self._last = t
                self._peak = max(self._peak, share)
                if both is not None:
                    self._masked = max(self._masked, float(both.mean()))
            else:
                self._close()
                self._open(frame, mask, t)

    @staticmethod
    def _judgeable(frame: Any, mask: Any) -> bool:
        open_cells = frame[~mask] if mask is not None else frame.ravel()
        if open_cells.size < FREEZE_MIN_OPEN_SHARE * frame.size:
            return False
        return float(open_cells.std()) >= FREEZE_FLAT_STD

    def finish(self) -> list[dict]:
        self._close()
        return list(self.runs)


def analyze_freezes(path: Any, *, mask_at: Callable[[float], Any] | None = None,
                    skip_at: Callable[[float], bool] | None = None) -> dict:
    """Застывшие куски файла отдельным проходом (без вспышек): ``{runs, seconds, frames}``."""
    from pathlib import Path

    from app.quality_lint_media import read_frames

    scanner = FreezeScanner(FREEZE_FPS, mask_at=mask_at, skip_at=skip_at)
    frames = 0
    for chunk in read_frames(Path(path), FREEZE_FPS):
        frames += len(chunk)
        scanner.feed(gray_cells(chunk))
    if not frames:
        raise RuntimeError(f"в {Path(path).name} нет кадров")
    return {"runs": scanner.finish(), "frames": frames, "seconds": round(frames / FREEZE_FPS, 2)}


def subsample_feed(scanner: FreezeScanner, src_fps: float) -> Callable[[Any], None]:
    """Кормить сканер из общего прохода вспышек (кадры по ``src_fps``): каждый ``src_fps/scanner.fps``-й кадр."""
    step = max(1, int(round(float(src_fps) / scanner.fps)))
    seen = [0]

    def feed(chunk: Any) -> None:
        offset = (-seen[0]) % step
        seen[0] += len(chunk)
        picked = chunk[offset::step]
        if len(picked):
            scanner.feed(gray_cells(picked))

    return feed


# ---------------------------------------------------------------- оверлеи клипа → клетки


def _rect_cells(rect: tuple[float, float, float, float], size: int, pad: int = FREEZE_MASK_PAD) -> tuple[slice, slice] | None:
    """Прямоугольник в долях кадра (x0, y0, x1, y1) → срезы клеток сетки ``size``×``size`` с запасом."""
    x0, y0, x1, y1 = rect
    if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1 or x1 <= x0 or y1 <= y0:
        return None
    c0 = max(0, int(math.floor(x0 * size)) - pad)
    c1 = min(size, int(math.ceil(x1 * size)) + pad)
    r0 = max(0, int(math.floor(y0 * size)) - pad)
    r1 = min(size, int(math.ceil(y1 * size)) + pad)
    return slice(r0, r1), slice(c0, c1)


def overlay_rects(layers: list[dict], t: float, *, camera: Any = None, vertical: bool = True,
                  caption_pages: dict[str, list[dict]] | None = None, ass: dict | None = None) -> list[tuple]:
    """Оверлеи клипа в момент ``t`` (шкала clip) — прямоугольники в долях кадра.

    9:16 — точные рамки слоёв (``quality_lint_zones.layer_box``: кегль, ключи, камера). Другие форматы —
    рамка с запасом вокруг x/y слоя (кегль там в px опорного кадра другого размера). Субтитры
    (слой ``captions`` и прожигаемые ASS) — полоса на их высоте, пока видна страница."""
    from app.quality_lint_base import _num
    from app.quality_lint_zones import H, W, ZONE_TYPES, layer_box

    out: list[tuple] = []
    for layer in layers:
        kind = layer.get("type")
        start, end = _num(layer.get("start")), _num(layer.get("end"), math.inf)
        if not start - 1e-6 <= t < end:
            continue
        if kind == "captions":
            pages = (caption_pages or {}).get(str(layer.get("id")))
            if pages is not None and not any(p["t0"] - 0.1 <= t <= p["t1"] + 0.1 for p in pages):
                continue
            y = _num(layer.get("y"), 0.68)
            out.append((0.0, y - CAPTION_BAND, 1.0, y + CAPTION_BAND))
            continue
        if kind not in ZONE_TYPES:
            continue
        if vertical:
            box = layer_box(layer, t, camera)
            if box is not None:
                out.append((box[0] / W, box[1] / H, box[2] / W, box[3] / H))
            continue
        x, y = _num(layer.get("x"), 0.5), _num(layer.get("y"), 0.5)
        out.append((x - 0.32, y - 0.12, x + 0.32, y + 0.12))
    if ass and any(p["t0"] - 0.1 <= t <= p["t1"] + 0.1 for p in ass.get("pages") or []):
        base = 1.0 - _num(ass.get("bottom_pct"), 12.0) / 100.0
        out.append((0.0, base - 2 * CAPTION_BAND, 1.0, base + 0.02))
    return out


def mask_from_rects(rects: list[tuple], size: int) -> Any:
    import numpy as np

    mask = np.zeros((size, size), dtype=bool)
    for rect in rects:
        cells = _rect_cells(rect, size)
        if cells is not None:
            mask[cells] = True
    return mask


def clip_mask_fn(layers: list[dict], clock: Any, *, size: int, camera: Any = None, vertical: bool = True,
                 caption_pages: dict[str, list[dict]] | None = None, ass: dict | None = None,
                 freezes: list[dict] | None = None, step: float = 0.2) -> Callable[[float], Any] | None:
    """``mask_at(u)`` для сканера: u — секунды готового файла (со стоп-кадрами) → клетки под оверлеями.
    Кэш по шагу ``step`` (слои за 0.2 с почти не двигаются, а рамка с запасом)."""
    if not layers and not ass:
        return None
    from app.freeze import post_to_pre

    cache: dict[int, Any] = {}

    def mask_at(u: float) -> Any:
        key = int(u / step)
        if key not in cache:
            pre = post_to_pre(key * step, freezes)["layers"] if freezes else key * step
            t = _clip_time(clock, pre)
            cache[key] = mask_from_rects(overlay_rects(layers, t, camera=camera, vertical=vertical,
                                                       caption_pages=caption_pages, ass=ass), size)
        return cache[key]

    return mask_at


def clip_skip_fn(clock: Any, freezes: list[dict] | None) -> Callable[[float], bool]:
    """``skip_at(u)``: нарочный стоп-кадр клипа (``freeze_frame``) или стоп-кадр в конце (``set_end_hold`` —
    всё, что после конца ролика со стоп-кадрами)."""
    from app.freeze import finished_with_freezes, post_to_pre

    final = float(clock.final_duration) if clock is not None else math.inf
    main_end = finished_with_freezes(final, freezes) if freezes and math.isfinite(final) else final

    def skip_at(u: float) -> bool:
        if u >= main_end - 0.02:
            return True
        return bool(freezes) and post_to_pre(u, freezes)["freeze"] is not None

    return skip_at


def _clip_time(clock: Any, final_t: float) -> float:
    if clock is None:
        return float(final_t)
    try:
        return clock.final_to_clip(min(float(final_t), clock.final_duration))
    except Exception:  # noqa: BLE001
        return float(final_t)


def _piece_note(clock: Any, segments: list[dict], speeds: dict[Any, float], t: float) -> tuple[int | None, str]:
    if clock is None or not segments:
        return None, ""
    try:
        index = clock.piece_at_clip(t)
    except Exception:  # noqa: BLE001
        return None, ""
    if not 0 <= index < len(segments):
        return None, ""
    speed = speeds.get(segments[index].get("id"))
    note = f"кусок {index + 1}" + (f" замедлен до ×{speed:g}" if speed is not None and speed < 1 else
                                    f" ускорен до ×{speed:g}" if speed is not None and speed > 1 else "")
    return index, note


def merge_runs(runs: list[dict], gap: float = FREEZE_MERGE_GAP) -> list[dict]:
    """Соседние застывшие отрезки (промежуток ≤ ``gap``) — один: ``duration`` — сумма застывшего, края — общие."""
    out: list[dict] = []
    for run in sorted(runs, key=lambda r: r["start"]):
        if out and run["start"] - out[-1]["end"] <= gap + 1e-6:
            last = out[-1]
            last["end"] = max(last["end"], run["end"])
            last["duration"] = round(last["duration"] + run["duration"], 2)
            last["moving_share"] = max(last.get("moving_share", 0.0), run.get("moving_share", 0.0))
            last["masked_share"] = max(last.get("masked_share", 0.0), run.get("masked_share", 0.0))
            last["parts"] = last.get("parts", 1) + 1
        else:
            out.append(dict(run))
    return out


def freeze_findings(runs: list[dict], clock: Any = None, *, segments: list[dict] | None = None,
                    speeds: dict[Any, float] | None = None, freezes: list[dict] | None = None) -> list[dict]:
    """Застывшие отрезки (секунды файла) → находки ``freezes`` (warn) с t/until на шкале clip."""
    from app.freeze import post_to_pre

    out: list[dict] = []
    for run in sorted(merge_runs(runs), key=lambda r: -r["duration"])[:MAX_FREEZE_FINDINGS]:
        a, b = float(run["start"]), float(run["end"])
        pre_a = post_to_pre(a, freezes)["video"] if freezes else a
        pre_b = post_to_pre(b, freezes)["video"] if freezes else b
        t, until = _clip_time(clock, pre_a), _clip_time(clock, pre_b)
        piece, note = _piece_note(clock, segments or [], speeds or {}, (t + until) / 2)
        masked = f", без оверлеев ({run['masked_share']:.0%} кадра под слоями)" if run.get("masked_share") else ""
        fix = ("вырежи застывшее (apply_cuts) или подрежь кусок (trim_piece); если кусок замедлен — верни скорость "
               "ближе к 1× (set_segment_speed, у мувика 0.87–1.15×), под текст не замедляй; застывший конец записи — "
               "проверь, что её fps прочитан из файла; нужен стоп-кадр — ставь freeze_frame осознанно")
        out.append(_finding(
            "freezes", "warn",
            f"картинка почти стоит {run['duration']:.1f} с на {a:.1f}–{b:.1f} с готового ролика"
            + (", с короткими перерывами" if run.get("parts", 1) > 1 else "")
            + (f" ({note})" if note else "") + masked
            + f" — зритель видит «клип остановился» (порог {FREEZE_MIN_SEC:g} с)",
            fix, t=t, until=until, final_t=round(a, 2), final_until=round(b, 2), duration=run["duration"],
            piece=piece))
    out.sort(key=lambda f: f.get("final_t", 0.0))
    return out


__all__ = ["FREEZE_FPS", "FREEZE_MIN_SEC", "FREEZE_CELL_DIFF", "FREEZE_MOVING_SHARE", "FreezeScanner",
           "analyze_freezes", "clip_mask_fn", "clip_skip_fn", "freeze_findings", "gray_cells", "mask_from_rects",
           "overlay_rects", "subsample_feed"]
